from collections.abc import Iterable

from feelancer.data.db import FeelancerDB

from .models import Base, HTLCAttempt


class PaymentTrackerStore:

    def __init__(self, db: FeelancerDB) -> None:
        self.db = db
        self.db.create_base(Base)

    def store_htlcs(self, htlcs: Iterable[HTLCAttempt]) -> None:

        pass
