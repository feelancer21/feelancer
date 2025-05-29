from collections.abc import Callable, Generator
from datetime import datetime

import pytz
from google.protobuf.json_format import MessageToDict

from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.tracker.data import TrackerStore
from feelancer.tracker.models import UntransformedData, UntransformedStreamType

from .base import LndBaseTracker


class LNDPeerEventTracker(LndBaseTracker):
    def __init__(self, lnd: LNDClient, store: TrackerStore):
        super().__init__(lnd, store, "peer events", 1)

    def _delete_orphaned_data(self) -> None:
        return None

    def _pre_sync_source(self) -> None:
        return None

    def _process_item_stream(
        self, item: ln.PeerEvent, recon_running: bool
    ) -> Generator[UntransformedData]:

        return self._process_peer_event(item, recon_running)

    def _new_recon_source(self) -> None:
        return None

    def _get_new_stream(self) -> Callable[[], Generator[UntransformedData]]:
        dispatcher = self._lnd.peer_event_dispatcher
        return self._get_new_stream_from_dispatcher(dispatcher)

    def _process_peer_event(
        self, event: ln.PeerEvent, recon_running: bool
    ) -> Generator[UntransformedData]:

        yield UntransformedData(
            ln_node_id=self._store.ln_node_id,
            stream_type=UntransformedStreamType.PEER_EVENT,
            data=MessageToDict(event),
            capture_time=datetime.now(pytz.utc),
        )
