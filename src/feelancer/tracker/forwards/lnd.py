from collections.abc import Callable, Generator
from datetime import datetime

from feelancer.grpc.client import StreamConverter
from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.retry import create_retry_handler
from feelancer.tracker.data import TrackerStore, create_operation_from_htlcs
from feelancer.tracker.lnd import LndBaseTracker
from feelancer.tracker.models import (
    Forward,
    ForwardResolveInfo,
    ForwardResolveType,
    HtlcDirectionType,
    HtlcForward,
    HtlcResolveInfoSettled,
    HtlcResolveType,
    HtlcType,
    Operation,
    TransactionType,
)
from feelancer.utils import ns_to_datetime

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds
PAGINATOR_BLOCKING_INTERVAL = 21  # 21 seconds

EXCEPTIONS_RETRY = (Exception,)
EXCEPTIONS_RAISE = ()
MAX_RETRIES = 3
DELAY = 3
MIN_TOLERANCE_DELTA = None

type LndForwardReconSource = StreamConverter[Operation, ln.ForwardingEvent]


htlc_event_retry_handler = create_retry_handler(
    exceptions_retry=EXCEPTIONS_RETRY,
    exceptions_raise=EXCEPTIONS_RAISE,
    max_retries=MAX_RETRIES,
    delay=DELAY,
    min_tolerance_delta=MIN_TOLERANCE_DELTA,
)


class LNDFwdTracker(LndBaseTracker):
    def __init__(
        self,
        lnd: LNDClient,
        store: TrackerStore,
        fwds_from_event_stream: Callable[
            [str, str, int, int], tuple[HtlcForward, HtlcForward]
        ],
    ):
        super().__init__(lnd, store)
        self._fwds_from_event_stream = fwds_from_event_stream

    def _delete_orphaned_data(self) -> None:
        return None

    def _get_items_name(self) -> str:
        return "forwards"

    def _pre_sync_source(self) -> LndForwardReconSource:
        index_offset = self._store.get_count_forwarding_events()
        self._logger.debug(f"Starting from index {index_offset} for {self._pub_key}")

        paginator = self._lnd.paginate_forwarding_events(index_offset=index_offset)

        return StreamConverter(
            paginator, lambda item: self._process_forwarding_event(item, False)
        )

    def _process_item_stream(
        self,
        item: ln.ForwardingEvent,
        recon_running: bool,
    ) -> Generator[Operation]:

        return self._process_forwarding_event(item, recon_running)

    def _new_recon_source(self) -> None:
        return None

    def _get_new_stream(self) -> Callable[..., Generator[Forward]]:

        return self._get_new_stream_from_paginator(
            lambda offset: self._lnd.paginate_forwarding_events(
                index_offset=offset, blocking_sec=PAGINATOR_BLOCKING_INTERVAL
            ),
            self._store.get_count_forwarding_events,
        )

    def _process_forwarding_event(
        self, fwd: ln.ForwardingEvent, recon_running: bool
    ) -> Generator[Operation]:

        try:
            # We are doing to a callback to the SubscribeHtlcEvent-Stream to get more
            # information about the forward.
            # This makes only sense if the forward time is after the start time.
            # Otherwise we cannot expect that the other stream has data available.
            fwd_time = ns_to_datetime(fwd.timestamp_ns)
            if self._time_start is None or fwd_time < self._time_start:
                raise Exception("No htlc events expected.")

            forward = self._forward_from_htlc_event(fwd)

        except Exception:
            # If any Exception occurs, we will generate a forward with the data
            # we have from the ForwardingEvent.
            forward = self._forward_from_fwd_event(fwd)

        yield create_operation_from_htlcs(
            txs=[forward], htlcs=[forward.htlc_in, forward.htlc_out]
        )

    def _forward_from_htlc_event(self, fwd: ln.ForwardingEvent) -> Forward:

        htlc_in, htlc_out = self._fwds_from_event_stream(
            str(fwd.chan_id_in), str(fwd.chan_id_out), fwd.amt_in_msat, fwd.amt_out_msat
        )
        resolve_time = htlc_out.resolve_info.resolve_time

        return Forward(
            ln_node_id=self._store.ln_node_id,
            htlc_in=htlc_in,
            htlc_out=htlc_out,
            fee_msat=fwd.fee_msat,
            resolve_info=self._create_forward_resolve_info(resolve_time),
            transaction_type=TransactionType.LN_FORWARD,
        )

    def _forward_from_fwd_event(self, fwd: ln.ForwardingEvent) -> Forward:

        resolve_time = ns_to_datetime(fwd.timestamp_ns)

        htlc_in = self._create_htlc_forward(
            channel_id=str(fwd.chan_id_in),
            amt_msat=fwd.amt_in_msat,
            direction_type=HtlcDirectionType.INCOMING,
            resolve_time=resolve_time,
        )
        htlc_out = self._create_htlc_forward(
            channel_id=str(fwd.chan_id_out),
            amt_msat=fwd.amt_out_msat,
            direction_type=HtlcDirectionType.OUTGOING,
            resolve_time=resolve_time,
        )

        forward = Forward(
            ln_node_id=self._store.ln_node_id,
            htlc_in=htlc_in,
            htlc_out=htlc_out,
            fee_msat=fwd.fee_msat,
            resolve_info=self._create_forward_resolve_info(resolve_time),
            transaction_type=TransactionType.LN_FORWARD,
        )

        return forward

    def _create_forward_resolve_info(self, time: datetime) -> ForwardResolveInfo:
        """Create a resolve info for a forward."""

        return ForwardResolveInfo(
            resolve_time=time,
            resolve_type=ForwardResolveType.SETTLED,
        )

    def _create_htlc_forward(
        self,
        channel_id: str,
        amt_msat: int,
        direction_type: HtlcDirectionType,
        resolve_time: datetime,
    ) -> HtlcForward:
        """Create a htlc forward."""

        return HtlcForward(
            channel_id=channel_id,
            htlc_index=None,
            amt_msat=amt_msat,
            attempt_time=None,
            direction_type=direction_type,
            timelock=None,
            resolve_info=self._create_resolve_info(resolve_time),
            htlc_type=HtlcType.FORWARD,
        )

    def _create_resolve_info(self, resolve_time: datetime) -> HtlcResolveInfoSettled:
        """Create a resolve info for a htlc."""

        return HtlcResolveInfoSettled(
            resolve_time=resolve_time,
            resolve_type=HtlcResolveType.SETTLED,
            preimage=None,
        )
