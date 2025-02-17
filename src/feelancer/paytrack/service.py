import threading

from feelancer.data.db import FeelancerDB
from feelancer.lightning.data import LightningStore

from .data import PaymentTrackerStore
from .tracker import PaymentTracker


# A config. But it is only a dummy at the moment.
class PaytrackConfig:
    def __init__(self, config_dict: dict) -> None: ...


class PaytrackService:

    def __init__(
        self,
        db: FeelancerDB,
        payment_tracker: PaymentTracker,
        paytrack_config: PaytrackConfig,
    ) -> None:

        self.store = PaymentTrackerStore(db)
        self.ln_store = LightningStore(db, payment_tracker.pubkey_local)
        self.payment_tracker = payment_tracker
        self.paytrack_config = paytrack_config
        self.is_stopped: bool = False

    def start(self) -> None:

        thread = threading.Thread(target=self.payment_tracker.start)
        thread.start()

        gen_attempts = self.payment_tracker.generate_attempts(self.ln_store.ln_node_id)
        self.store.store_attempts(gen_attempts)

        thread.join()

    def stop(self) -> None:

        self.payment_tracker.stop()
        self.is_stopped = True
