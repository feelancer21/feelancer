from sqlalchemy import Select, select

from feelancer.data.db import FeelancerDB

from .models import Base, Payment


def query_payment(payment_hash: str) -> Select[tuple[Payment]]:
    qry = select(Payment).where(Payment.payment_hash == payment_hash)

    return qry


class PaymentTrackerStore:

    def __init__(self, db: FeelancerDB) -> None:
        self.db = db
        self.db.create_base(Base)

    def get_payment_id(self, payment_hash: str) -> int | None:
        """
        Returns the payment id for a given payment hash.
        """

        return self.db.query_first(query_payment(payment_hash), lambda p: p.id)

    def add_payment(self, payment: Payment) -> int:
        """
        Adds a payment to the database. Returns the id of the payment.
        """

        return self.db.add_post(payment, lambda p: p.id)
