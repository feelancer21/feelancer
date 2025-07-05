from collections.abc import Callable, Generator
from datetime import datetime

import pytz
from google.protobuf.json_format import MessageToDict

from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.tracker.data import TrackerStore
from feelancer.tracker.models import UntransformedData, UntransformedStreamType

from .base import LndBaseTracker


class LNDTransactionTracker(LndBaseTracker):
    def __init__(self, lnd: LNDClient, store: TrackerStore, store_events: bool = False):
        super().__init__(lnd, store, "onchain transactions", 1)

        self.store_events = store_events

    def _delete_orphaned_data(self) -> None:
        return None

    def _pre_sync_source(self) -> None:
        return None

    def _process_item_stream(
        self, item: ln.Transaction, recon_running: bool
    ) -> Generator[UntransformedData]:

        return self._process_transaction(item, recon_running)

    def _new_recon_source(self) -> None:
        return None

    def _get_new_stream(self) -> Callable[[], Generator[UntransformedData]]:
        dispatcher = self._lnd.transaction_dispatcher
        return self._get_new_stream_from_dispatcher(dispatcher)

    def _process_transaction(
        self, event: ln.Transaction, recon_running: bool
    ) -> Generator[UntransformedData]:

        if not self.store_events:
            return

        data = MessageToDict(event)
        self._logger.debug(f"Processing transaction: {data=}")

        yield UntransformedData(
            ln_node_id=self._store.ln_node_id,
            stream_type=UntransformedStreamType.TRANSACTION,
            data=data,
            capture_time=datetime.now(pytz.utc),
        )
