from feelancer.base import BaseServer, default_retry_handler
from feelancer.data.db import FeelancerDB
from feelancer.lightning.data import LightningStore

from .data import PaymentTrackerStore
from .tracker import PaymentTracker


# A config. But it is only a dummy at the moment.
class PaytrackConfig:
    def __init__(self, config_dict: dict) -> None: ...


class PaytrackService(BaseServer):
    """
    Receiving of payment data from a stream and storing in the database.
    """

    def __init__(
        self,
        db: FeelancerDB,
        payment_tracker: PaymentTracker,
        paytrack_config: PaytrackConfig,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._store = PaymentTrackerStore(db)
        self._ln_store = LightningStore(db, payment_tracker.pubkey_local)
        self._payment_tracker = payment_tracker
        self._paytrack_config = paytrack_config

        self._register_starter(self._start_server)
        self._register_stopper(self._stop_server)

    @default_retry_handler
    def _start_server(self) -> None:
        """Start of storing of payments in the store."""

        gen_attempts = self._payment_tracker.generate_attempts(
            self._ln_store.ln_node_id,
            self._store.get_payment_id,
            self._store.add_payment,
        )
        self._store.add_attempts(gen_attempts)

    def _stop_server(self) -> None:
        # Not implemented. Service ends when the incoming payment stream has
        # exhausted.
        return None
