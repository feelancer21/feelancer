from collections.abc import Callable, Generator
from datetime import datetime

import pytz
from google.protobuf.json_format import MessageToDict

from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.tracker.data import TrackerStore
from feelancer.tracker.models import UntransformedData, UntransformedStreamType

from .base import LndBaseTracker


class LNDChannelGraphTracker(LndBaseTracker):
    def __init__(self, lnd: LNDClient, store: TrackerStore):
        super().__init__(lnd, store, "graph topology updates")

    def _delete_orphaned_data(self) -> None:
        return None

    def _pre_sync_source(self) -> None:
        return None

    def _process_item_stream(
        self, item: ln.GraphTopologyUpdate, recon_running: bool
    ) -> Generator[UntransformedData]:

        return self._process_graph_topo_update(item, recon_running)

    def _new_recon_source(self) -> None:
        return None

    def _get_new_stream(self) -> Callable[[], Generator[UntransformedData]]:
        dispatcher = self._lnd.graph_topology_dispatcher
        return self._get_new_stream_from_dispatcher(dispatcher)

    def _process_graph_topo_update(
        self, event: ln.GraphTopologyUpdate, recon_running: bool
    ) -> Generator[UntransformedData]:

        yield UntransformedData(
            ln_node_id=self._store.ln_node_id,
            stream_type=UntransformedStreamType.GRAPH_TOPOLOGY,
            data=MessageToDict(event),
            capture_time=datetime.now(pytz.utc),
        )
