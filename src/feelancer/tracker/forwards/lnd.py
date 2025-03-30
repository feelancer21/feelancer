from collections.abc import Generator

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message

from feelancer.lnd.client import LndHtlcEventDispatcher
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.lnd.grpc_generated import router_pb2 as rt
from feelancer.tracker.lnd import LndBaseReconSource, LndBaseTracker
from feelancer.tracker.models import ForwardingEvent, HtlcEvent, HtlcEventType
from feelancer.utils import ns_to_datetime

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds
CHUNK_SIZE = 1000

ORM_OUT = ForwardingEvent | HtlcEvent


class LNDForwardTracker(LndBaseTracker):

    def _delete_orphaned_data(self) -> None:
        return None

    def _get_items_name(self) -> str:
        return "forwards"

    def _pre_sync_source(self) -> Generator[ln.ForwardingEvent]:

        index_offset = self._store.get_count_forwarding_events()
        self._logger.debug(f"Starting from index {index_offset} for {self._pub_key}")

        return self._lnd.paginate_forwarding_events(index_offset=index_offset)

    def _process_item_stream(
        self,
        item: rt.HtlcEvent,
        recon_running: bool,
    ) -> Generator[ORM_OUT]:

        return self._process_htlc_event(item, recon_running)

    def _process_item_pre_sync(
        self,
        item: ln.ForwardingEvent,
        recon_running: bool,
    ) -> Generator[ORM_OUT]:

        return self._process_forwarding_event(item, recon_running)

    def _new_recon_source(self) -> LndBaseReconSource[ORM_OUT, ln.ForwardingEvent]:

        index_offset = 2_100_000_000
        paginator = self._lnd.paginate_forwarding_events(index_offset=index_offset)

        return LndBaseReconSource(paginator, self._process_forwarding_event)

    def _new_dispatcher(self) -> LndHtlcEventDispatcher:
        return self._lnd.subscribe_htlc_events_dispatcher

    def _process_forwarding_event(
        self, fwd: ln.ForwardingEvent, recon_running: bool
    ) -> Generator[ORM_OUT]:

        yield ForwardingEvent(
            ln_node_id=self._store.ln_node_id,
            timestamp=ns_to_datetime(fwd.timestamp_ns),
            chan_id_in=fwd.chan_id_in,
            chan_id_out=fwd.chan_id_out,
            fee_msat=fwd.fee_msat,
            amt_in_msat=fwd.amt_in_msat,
            amt_out_msat=fwd.amt_out_msat,
        )

    def _process_htlc_event(
        self, htlc: rt.HtlcEvent, recon_running: bool
    ) -> Generator[ORM_OUT]:

        def _conv_msg_to_dict(msg: Message, field: str):
            if not msg.HasField(field):
                return None
            return MessageToDict(getattr(msg, field))

        yield HtlcEvent(
            ln_node_id=self._store.ln_node_id,
            timestamp=ns_to_datetime(htlc.timestamp_ns),
            incoming_channel_id=htlc.incoming_channel_id,
            outgoing_channel_id=htlc.outgoing_channel_id,
            incoming_htlc_id=htlc.incoming_htlc_id,
            outgoing_htlc_id=htlc.outgoing_htlc_id,
            event_type=HtlcEventType(htlc.event_type),
            forward_event=_conv_msg_to_dict(htlc, "forward_event"),
            forward_fail_event=_conv_msg_to_dict(htlc, "forward_fail_event"),
            settle_event=_conv_msg_to_dict(htlc, "settle_event"),
            link_fail_event=_conv_msg_to_dict(htlc, "link_fail_event"),
            subscribed_event=_conv_msg_to_dict(htlc, "subscribed_event"),
            final_htlc_event=_conv_msg_to_dict(htlc, "final_htlc_event"),
        )
