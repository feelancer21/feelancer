from collections.abc import Callable, Generator

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message

from feelancer.lnd.grpc_generated import router_pb2 as rt
from feelancer.tracker.lnd import LndBaseTracker
from feelancer.tracker.models import HtlcEvent, HtlcEventType
from feelancer.utils import ns_to_datetime

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds
CHUNK_SIZE = 1000


class LNDHtlcTracker(LndBaseTracker):

    def _delete_orphaned_data(self) -> None:
        return None

    def _get_items_name(self) -> str:
        return "htlcs"

    def _pre_sync_source(self) -> None:
        return None

    def _process_item_stream(
        self,
        item: rt.HtlcEvent,
        recon_running: bool,
    ) -> Generator[HtlcEvent]:

        return self._process_htlc_event(item, recon_running)

    def _process_item_pre_sync(
        self,
        item: rt.HtlcEvent,
        recon_running: bool,
    ) -> Generator[HtlcEvent]:

        return self._process_htlc_event(item, recon_running)

    def _new_recon_source(self) -> None:

        return None

    def _get_new_stream(self) -> Callable[..., Generator[HtlcEvent]]:
        dispatcher = self._lnd.subscribe_htlc_events_dispatcher
        return self._get_new_stream_from_dispatcher(dispatcher)

    def _process_htlc_event(
        self, htlc: rt.HtlcEvent, recon_running: bool
    ) -> Generator[HtlcEvent]:

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
