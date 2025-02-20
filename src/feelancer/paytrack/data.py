import functools

from sqlalchemy import Select, select

from feelancer.data.db import FeelancerDB

from .models import Base, Payment

CACHE_SIZE_PAYMENT_ID = 1024


class PaymentNotFound(Exception): ...


def query_payment(payment_hash: str) -> Select[tuple[Payment]]:
    qry = select(Payment).where(Payment.payment_hash == payment_hash)

    return qry


class PaymentTrackerStore:

    def __init__(self, db: FeelancerDB) -> None:
        self.db = db
        self.db.create_base(Base)

    @functools.lru_cache(maxsize=CACHE_SIZE_PAYMENT_ID)
    def get_payment_id(self, payment_hash: str) -> int:
        """
        Returns the payment id for a given payment hash.
        """

        id = self.db.query_first(query_payment(payment_hash), lambda p: p.id)
        if id is None:
            raise PaymentNotFound(f"Payment with hash {payment_hash} not found.")
        return id

    def add_payment(self, payment: Payment) -> int:
        """
        Adds a payment to the database. Returns the id of the payment.
        """

        return self.db.add_post(payment, lambda p: p.id)
