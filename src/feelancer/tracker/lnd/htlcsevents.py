import threading
from collections import defaultdict, deque
from collections.abc import Callable, Generator
from datetime import datetime
from typing import TypeVar

from google.protobuf.json_format import MessageToDict

from feelancer.grpc.utils import convert_msg_to_dict
from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.grpc_generated import router_pb2 as rt
from feelancer.tracker.data import TrackerStore, new_operation_from_htlcs
from feelancer.tracker.models import (
    FailureCode,
    FailureDetail,
    Forward,
    Htlc,
    HtlcDirectionType,
    HtlcEvent,
    HtlcEventType,
    HtlcForward,
    HtlcReceive,
    HtlcResolveInfoForwardFailed,
    HtlcResolveInfoLinkFailed,
    HtlcResolveInfoSettled,
    HtlcResolveType,
    HtlcType,
    Operation,
    TransactionResolveInfo,
    TransactionResolveType,
)
from feelancer.utils import bytes_to_str, ns_to_datetime

from .base import LndBaseTracker

T = TypeVar("T", bound=Htlc)

WAIT_TIME = 60

# type for a an index of forwarding events. Two use cases:
#   case1: chan_id_in, chan_id_out, htlc_id_in, htlc_id_out
#   case2: chan_id_in, chan_id_out, amt_in_msat, amt_out_msat
type FwdAmtIndex = tuple[str, str, int, int]
type HtlcIndex = tuple[int, int]
type DataGenerated = HtlcEvent | HtlcForward | HtlcReceive | Operation


def _incoming_htlc_index(htlc: rt.HtlcEvent) -> HtlcIndex:
    return (htlc.incoming_channel_id, htlc.incoming_htlc_id)


class LNDHtlcTracker(LndBaseTracker):
    def __init__(
        self, lnd: LNDClient, store: TrackerStore, store_htlc_events: bool = False
    ) -> None:
        super().__init__(lnd, store, "htlc events")

        self._store_htlc_events = store_htlc_events

        self._fwd_lock = threading.Lock()

        # Dictionary with all not final htlcs indexed by their incoming channel id
        # and htlc id. This dict is only managed by one thread-
        self._not_final_by_in: dict[HtlcIndex, list[rt.HtlcEvent]] = defaultdict(list)

        # We store all settled forwards in a dictionary. They are consumed
        # by the forward tracker to get more information about the forward.
        # The consumers don't know the htlc id, so we use chanel ids and amounts
        # to identify the forwards.
        # The values are deques, because we can have multiple forwards with the
        # same amount. The deque is used to keep the order of the events.
        # No stored in the database, i.e. will not survive a restart.
        self._fwd_settled: defaultdict[
            FwdAmtIndex, deque[tuple[HtlcForward, HtlcForward]]
        ] = defaultdict(deque)

        # forward tracker can ask before this tracker get the event
        # there fore we create a lock to wait for the event if first try fails.
        self._wait_for_fwd: tuple[FwdAmtIndex, threading.Event] | None = None

    def _delete_orphaned_data(self) -> None:
        return None

    def _pre_sync_source(self) -> None:
        return None

    def _process_item_stream(
        self,
        item: rt.HtlcEvent,
        recon_running: bool,
    ) -> Generator[DataGenerated]:

        return self._process_htlc_event(item, recon_running)

    def _new_recon_source(self) -> None:

        return None

    def _get_new_stream(self) -> Callable[[], Generator[HtlcEvent]]:
        dispatcher = self._lnd.htlc_events_dispatcher
        return self._get_new_stream_from_dispatcher(dispatcher)

    def _process_htlc_event(
        self, htlc: rt.HtlcEvent, recon_running: bool
    ) -> Generator[DataGenerated]:

        if htlc.event_type == rt.HtlcEvent.EventType.FORWARD:
            self._add_non_final_by_incoming(htlc)

        elif htlc.event_type == rt.HtlcEvent.EventType.RECEIVE:
            # Only interested in link fail events for RECEIVE. Settled are processed
            # with the invoice stream.
            if htlc.HasField("link_fail_event"):
                self._add_non_final_by_incoming(htlc)

        elif htlc.event_type == rt.HtlcEvent.EventType.UNKNOWN:
            # UNKNOWN should have a final htlc event, but we make a safety check
            if htlc.HasField("final_htlc_event"):
                yield from self._gen_from_final_htlc(htlc)

        if not self._store_htlc_events:
            return None

        yield HtlcEvent(
            ln_node_id=self._store.ln_node_id,
            timestamp=ns_to_datetime(htlc.timestamp_ns),
            incoming_channel_id=htlc.incoming_channel_id,
            outgoing_channel_id=htlc.outgoing_channel_id,
            incoming_htlc_id=htlc.incoming_htlc_id,
            outgoing_htlc_id=htlc.outgoing_htlc_id,
            event_type=HtlcEventType(htlc.event_type),
            forward_event=convert_msg_to_dict(htlc, "forward_event"),
            forward_fail_event=convert_msg_to_dict(htlc, "forward_fail_event"),
            settle_event=convert_msg_to_dict(htlc, "settle_event"),
            link_fail_event=convert_msg_to_dict(htlc, "link_fail_event"),
            subscribed_event=convert_msg_to_dict(htlc, "subscribed_event"),
            final_htlc_event=convert_msg_to_dict(htlc, "final_htlc_event"),
        )

    def _cache_fwd_htlcs(self, htlc_in: HtlcForward, htlc_out: HtlcForward) -> None:
        index = (
            htlc_in.channel_id,
            htlc_out.channel_id,
            htlc_in.amt_msat,
            htlc_out.amt_msat,
        )
        with self._fwd_lock:
            self._fwd_settled[index].append((htlc_in, htlc_out))

            self._logger.trace(
                f"Added fwd htlc: {index=}; {htlc_in.__dict__=}; {htlc_out.__dict__=}; "
                f"{len(self._fwd_settled)=}; "
                f"{len(self._fwd_settled[index])=}; "
            )

            if self._wait_for_fwd and self._wait_for_fwd[0] == index:
                self._wait_for_fwd[1].set()

    def _gen_from_final_htlc(
        self, final_htlc: rt.HtlcEvent
    ) -> Generator[DataGenerated]:

        index_in = _incoming_htlc_index(final_htlc)
        events_in = self._not_final_by_in.pop(index_in, None)

        if events_in is None:
            return None

        if len(events_in) == 2:
            args = (events_in[0], events_in[1], final_htlc)
        elif len(events_in) == 1:
            args = (events_in[0], None, final_htlc)
        elif len(events_in) == 3 and events_in[1].HasField("forward_event"):
            # Sometimes lnd yields the forward_event twice (maybe a bug?)
            # we skip the second message
            args = (events_in[0], events_in[2], final_htlc)
        else:
            # Should not happen but we log this case
            self._logger.warning(
                f"Cannot process final htlc; {len(events_in)=}; "
                f"{index_in=}; {MessageToDict(final_htlc)=}"
            )
            return None

        try:
            yield from self._process_incoming_events(*args)
        except Exception as e:
            self._logger.error(e)

    def _process_incoming_events(
        self,
        incoming_accepted: rt.HtlcEvent,
        forward_resolved: rt.HtlcEvent | None,
        final: rt.HtlcEvent,
    ) -> Generator[DataGenerated]:

        def _log_msg() -> str:
            """htlc events as strings for logging"""
            if not forward_resolved:
                return f"{MessageToDict(incoming_accepted)=}; {MessageToDict(final)=}"
            else:
                return (
                    f"{MessageToDict(incoming_accepted)=}; "
                    f"{MessageToDict(forward_resolved)=}; {MessageToDict(final)=}"
                )

        # None if we receive a forward fail event with the first message
        htlc_info: rt.HtlcInfo | None = None
        if incoming_accepted.HasField("forward_event"):
            htlc_info = incoming_accepted.forward_event.info
        elif incoming_accepted.HasField("link_fail_event"):
            htlc_info = incoming_accepted.link_fail_event.info
        elif incoming_accepted.HasField("forward_fail_event"):
            htlc_info = None
        elif incoming_accepted.HasField("settle_event"):
            # We have missed the forward event, maybe due to restart.
            return None

        else:
            raise ValueError(f"Cannot determine htlc info for {_log_msg()}")

        h_in: Htlc | None = None
        h_out: Htlc | None = None
        if incoming_accepted.event_type == rt.HtlcEvent.EventType.RECEIVE:
            h_in = self._new_incoming_htlc(HtlcReceive, htlc_info, incoming_accepted)

            if incoming_accepted.HasField("link_fail_event"):
                h_in.resolve_info = self._new_link_fail(
                    htlc=incoming_accepted,
                    resolve_time=ns_to_datetime(final.timestamp_ns),
                    direction_failed=HtlcDirectionType.INCOMING,
                )
                yield h_in
                return None

        if incoming_accepted.event_type == rt.HtlcEvent.EventType.FORWARD:
            h_in = self._new_incoming_htlc(HtlcForward, htlc_info, incoming_accepted)

            if incoming_accepted.HasField("link_fail_event"):
                h_in.resolve_info = self._new_link_fail(
                    htlc=incoming_accepted,
                    resolve_time=ns_to_datetime(final.timestamp_ns),
                    direction_failed=HtlcDirectionType.OUTGOING,
                )
                yield h_in
                return None

            if incoming_accepted.HasField("forward_fail_event"):
                # If forward faul event is the first message, we don't have an
                # outgoing htlc and assume fail on the incoming channel
                h_in.resolve_info = self._new_forward_fail(
                    resolve_time=ns_to_datetime(final.timestamp_ns),
                    direction_failed=HtlcDirectionType.INCOMING,
                )
                yield h_in
                return None

            if forward_resolved is not None:
                h_out = self._new_outgoing_htlc(
                    HtlcForward, htlc_info, forward_resolved
                )

                if forward_resolved.HasField("settle_event"):
                    preimage = bytes_to_str(forward_resolved.settle_event.preimage)

                    h_in.resolve_info = self._new_settle_info(
                        resolve_time=ns_to_datetime(final.timestamp_ns),
                        preimage=preimage,
                    )

                    h_out.resolve_info = self._new_settle_info(
                        resolve_time=ns_to_datetime(forward_resolved.timestamp_ns),
                        preimage=preimage,
                    )

                    self._cache_fwd_htlcs(h_in, h_out)
                    return None

                elif forward_resolved.HasField("link_fail_event"):
                    # It happens that the first message is of type FORWARD and
                    # the second message fails the link with type RECEIVE.
                    if forward_resolved.event_type == rt.HtlcEvent.EventType.RECEIVE:
                        h_in.resolve_info = self._new_link_fail(
                            htlc=forward_resolved,
                            resolve_time=ns_to_datetime(final.timestamp_ns),
                            direction_failed=HtlcDirectionType.INCOMING,
                        )
                        yield h_in
                        return None

                elif forward_resolved.HasField("forward_fail_event"):
                    h_in.resolve_info = self._new_forward_fail(
                        resolve_time=ns_to_datetime(final.timestamp_ns),
                        direction_failed=HtlcDirectionType.OUTGOING,
                    )

                    h_out.resolve_info = self._new_forward_fail(
                        resolve_time=ns_to_datetime(forward_resolved.timestamp_ns),
                        direction_failed=HtlcDirectionType.OUTGOING,
                    )

                    fwd_resolve_info = TransactionResolveInfo(
                        resolve_time=ns_to_datetime(final.timestamp_ns),
                        resolve_type=TransactionResolveType.FAILED,
                    )

                    fee_mast = 0
                    if htlc_info:
                        fee_mast = (
                            htlc_info.incoming_amt_msat - htlc_info.outgoing_amt_msat
                        )

                    uuid = Forward.generate_uuid(
                        ln_node_id=self._store.ln_node_id,
                        tx_index=[
                            h_in.channel_id,
                            h_in.htlc_index,
                            h_out.channel_id,
                            h_out.htlc_index,
                        ],
                    )

                    forward = Forward(
                        uuid=uuid,
                        ln_node_id=self._store.ln_node_id,
                        htlc_in=h_in,
                        htlc_out=h_out,
                        fee_msat=fee_mast,
                        resolve_info=fwd_resolve_info,
                        creation_time=h_in.attempt_time,
                    )

                    yield new_operation_from_htlcs([forward], [])
                    return None

        # Raise an error message if our htlcs haven't matched to one of the
        # patterns above.
        raise ValueError(f"Cannot process htlc events for {_log_msg()}")

    def _new_incoming_htlc(
        self, cls: type[T], htlc_info: rt.HtlcInfo | None, htlc: rt.HtlcEvent
    ) -> T:

        return cls(
            channel_id=str(htlc.incoming_channel_id),
            htlc_index=htlc.incoming_htlc_id,
            amt_msat=htlc_info.incoming_amt_msat if htlc_info else None,
            attempt_time=ns_to_datetime(htlc.timestamp_ns),
            direction_type=HtlcDirectionType.INCOMING,
            timelock=htlc_info.incoming_timelock if htlc_info else None,
            htlc_type=HtlcType(htlc.event_type),
        )

    def _new_outgoing_htlc(
        self, cls: type[T], htlc_info: rt.HtlcInfo | None, htlc: rt.HtlcEvent
    ) -> T:

        return cls(
            channel_id=str(htlc.outgoing_channel_id),
            htlc_index=htlc.outgoing_htlc_id,
            amt_msat=htlc_info.outgoing_amt_msat if htlc_info else None,
            attempt_time=ns_to_datetime(htlc.timestamp_ns),
            direction_type=HtlcDirectionType.OUTGOING,
            timelock=htlc_info.outgoing_timelock if htlc_info else None,
            htlc_type=HtlcType(htlc.event_type),
        )

    def _new_link_fail(
        self,
        htlc: rt.HtlcEvent,
        resolve_time: datetime,
        direction_failed: HtlcDirectionType,
    ) -> HtlcResolveInfoLinkFailed:

        link_fail = htlc.link_fail_event

        if direction_failed == HtlcDirectionType.INCOMING:
            link_failed = str(htlc.incoming_channel_id)
        else:
            link_failed = str(htlc.outgoing_channel_id)

        return HtlcResolveInfoLinkFailed(
            resolve_time=resolve_time,
            resolve_type=HtlcResolveType.LINK_FAILED,
            wire_failure=FailureCode(link_fail.wire_failure),
            failure_detail=FailureDetail(link_fail.failure_detail),
            failure_string=link_fail.failure_string,
            direction_failed=direction_failed,
            link_failed=link_failed,
        )

    def _new_settle_info(
        self, resolve_time: datetime, preimage: str
    ) -> HtlcResolveInfoSettled:
        return HtlcResolveInfoSettled(
            resolve_time=resolve_time,
            resolve_type=HtlcResolveType.SETTLED,
            preimage=preimage,
        )

    def _new_forward_fail(
        self, resolve_time: datetime, direction_failed: HtlcDirectionType
    ) -> HtlcResolveInfoForwardFailed:
        return HtlcResolveInfoForwardFailed(
            resolve_time=resolve_time,
            resolve_type=HtlcResolveType.FORWARD_FAILED,
            direction_failed=direction_failed,
        )

    def _add_non_final_by_incoming(self, htlc: rt.HtlcEvent) -> None:
        index = _incoming_htlc_index(htlc)
        self._not_final_by_in[index].append(htlc)

        self._logger.trace_lazy(
            lambda: f"Added non final event: {index=}; event={MessageToDict(htlc)}; "
            f"{len(self._not_final_by_in)=}; "
        )

    def pop_settled_forwards(
        self, chan_id_in: str, chan_id_out: str, amt_in_msat: int, amt_out_msat: int
    ) -> tuple[HtlcForward, HtlcForward]:
        """
        Returns all settled forward events.
        """
        fwd_amt_index = (chan_id_in, chan_id_out, amt_in_msat, amt_out_msat)

        # First try to get the events from the cache
        try:
            res = self._pop_settled_forwards(fwd_amt_index)
            return res

        except Exception as e:
            self._logger.warning(f"{e}; wait for retry in max {WAIT_TIME} seconds")
            with self._fwd_lock:
                self._wait_for_fwd = (fwd_amt_index, threading.Event())

        # Second try to get the events from the cache
        try:
            self._wait_for_fwd[1].wait(WAIT_TIME)
            res = self._pop_settled_forwards(fwd_amt_index)
        finally:
            # reset of the tuple
            self._wait_for_fwd = None

        return res

    def _pop_settled_forwards(
        self, fwd_amt_index: FwdAmtIndex
    ) -> tuple[HtlcForward, HtlcForward]:

        with self._fwd_lock:
            if fwd_amt_index not in self._fwd_settled:
                raise KeyError(f"Cannot find htlc events for forward {fwd_amt_index=}")

            queue = self._fwd_settled[fwd_amt_index]
            res = queue.popleft()

            if len(queue) == 0:
                del self._fwd_settled[fwd_amt_index]

        return res

    def set_store_htlc_events(self, store: bool) -> None:
        """
        Set whether to store htlc events in the database.
        """
        self._store_htlc_events = store
