import logging
import time

from feelancer.data.db import FeelancerDB

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

        self.db = db
        self.payment_tracker = payment_tracker
        self.paytrack_config = paytrack_config
        self.is_stopped: bool = False

    def start(self) -> None:

        self.payment_tracker.start()

        while not self.is_stopped:
            logging.debug("Paytrack Service running")
            time.sleep(1)

    def stop(self) -> None:

        self.payment_tracker.stop()
        self.is_stopped = True
