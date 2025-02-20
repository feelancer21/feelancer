from feelancer.data.db import FeelancerDB
from feelancer.lightning.data import LightningStore

from .data import PaymentTrackerStore
from .tracker import PaymentTracker


# A config. But it is only a dummy at the moment.
class PaytrackConfig:
    def __init__(self, config_dict: dict) -> None: ...


class PaytrackService:
    """
    Receiving of payment data from a stream and storing in the database.
    """

    def __init__(
        self,
        db: FeelancerDB,
        payment_tracker: PaymentTracker,
        paytrack_config: PaytrackConfig,
    ) -> None:

        self._store = PaymentTrackerStore(db)
        self._ln_store = LightningStore(db, payment_tracker.pubkey_local)
        self._payment_tracker = payment_tracker
        self._paytrack_config = paytrack_config

    def start(self) -> None:
        """Start of storing of payments in the store."""

        gen_attempts = self._payment_tracker.generate_attempts(
            self._ln_store.ln_node_id,
            self._store.get_payment_id,
            self._store.add_payment,
        )
        self._store.db.add_all_from_iterable(gen_attempts)

    def stop(self) -> None:
        # Not implemented. Service ends when the incoming payment stream has
        # exhausted.
        return None
