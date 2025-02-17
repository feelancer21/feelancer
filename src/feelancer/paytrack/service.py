import hashlib
import logging
import threading

from feelancer.data.db import FeelancerDB

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
        self.payment_tracker = payment_tracker
        self.paytrack_config = paytrack_config
        self.is_stopped: bool = False

    def start(self) -> None:

        thread = threading.Thread(target=self.payment_tracker.start)
        thread.start()

        for a in self.payment_tracker.generate_attempts():
            hashsum = hashlib.sha256(
                a.SerializePartialToString(deterministic=True)
            ).hexdigest()

            hashsum_route = hashlib.sha256(
                a.route.SerializePartialToString(deterministic=True)
            ).hexdigest()

            logging.debug(
                f"attempt: {a.attempt_id}, status: {a.status}, attempt_time: {a.attempt_time_ns}, resolve_time_ns: {a.resolve_time_ns}, failure: {a.failure.code}, hashsum: {hashsum}, hashsum_route: {hashsum_route}"
            )

        thread.join()

    def stop(self) -> None:

        self.payment_tracker.stop()
        self.is_stopped = True
