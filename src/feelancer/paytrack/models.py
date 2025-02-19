"""
Database Model for PaymentTracker. Inspired by lnd payment protos.
"""

from __future__ import annotations

import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from feelancer.lightning.models import Base, DBLnNode


class HTLCStatus(PyEnum):
    IN_FLIGHT = 0
    SUCCEEDED = 1
    FAILED = 2


# Failure code as defined in the Lightning spec
class FailureCode(PyEnum):
    RESERVED = 0
    INCORRECT_OR_UNKNOWN_PAYMENT_DETAILS = 1
    INCORRECT_PAYMENT_AMOUNT = 2
    FINAL_INCORRECT_CLTV_EXPIRY = 3
    FINAL_INCORRECT_HTLC_AMOUNT = 4
    FINAL_EXPIRY_TOO_SOON = 5
    INVALID_REALM = 6
    EXPIRY_TOO_SOON = 7
    INVALID_ONION_VERSION = 8
    INVALID_ONION_HMAC = 9
    INVALID_ONION_KEY = 10
    AMOUNT_BELOW_MINIMUM = 11
    FEE_INSUFFICIENT = 12
    INCORRECT_CLTV_EXPIRY = 13
    CHANNEL_DISABLED = 14
    TEMPORARY_CHANNEL_FAILURE = 15
    REQUIRED_NODE_FEATURE_MISSING = 16
    REQUIRED_CHANNEL_FEATURE_MISSING = 17
    UNKNOWN_NEXT_PEER = 18
    TEMPORARY_NODE_FAILURE = 19
    PERMANENT_NODE_FAILURE = 20
    PERMANENT_CHANNEL_FAILURE = 21
    EXPIRY_TOO_FAR = 22
    MPP_TIMEOUT = 23
    INVALID_ONION_PAYLOAD = 24
    INVALID_ONION_BLINDING = 25
    INTERNAL_FAILURE = 997
    UNKNOWN_FAILURE = 998
    UNREADABLE_FAILURE = 999


class HTLCAttempt(Base):
    __tablename__ = "payment_htlc_attempt"
    __table_args__ = (UniqueConstraint("ln_node_id", "attempt_id", "status"),)

    # unique identifier of the attempt
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # the id of the local lightning node
    ln_node_id: Mapped[int] = mapped_column(ForeignKey("ln_node.id"), nullable=False)

    # the local lightning node
    ln_node: Mapped[DBLnNode] = relationship(DBLnNode)

    # The unique ID that is used for this attempt.
    attempt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The status of the HTLC.
    status: Mapped["HTLCStatus"] = mapped_column(Enum(HTLCStatus), nullable=False)

    # The time in UNIX nanoseconds at which this HTLC was sent.
    attempt_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # The time in UNIX nanoseconds at which this HTLC was settled or failed.
    # This field is nullable since it might not be set if the HTLC is still in flight.
    resolve_time: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    failure_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("payment_failure.id"), nullable=True
    )
    failure: Mapped[Failure] = relationship("Failure")

    hops: Mapped[list[Hop]] = relationship("Hop", back_populates="attempt")

    # Number of hops in the route.
    hops_num: Mapped[int] = mapped_column(Integer, nullable=False)

    # The sha256sum of the concatenation of all public keys of the route.
    hops_sha256_sum: Mapped[String] = mapped_column(String(64), nullable=False)


class Failure(Base):
    __tablename__ = "payment_failure"

    # unique identifier of the failure
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # Failure code as defined in the Lightning spec.
    code: Mapped["FailureCode"] = mapped_column(Enum(FailureCode), nullable=False)

    # The position in the path of the intermediate or final node that generated
    # the failure message. Position zero is the sender node.
    source_index: Mapped[int] = mapped_column(BigInteger)

    # source hops is added in an sql update after initial insert.
    # That's why nullable is True.
    source_hop_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("payment_hop.id"), nullable=True
    )
    source_hop: Mapped[Hop] = relationship("Hop", post_update=True)


class Hop(Base):
    __tablename__ = "payment_hop"
    __table_args__ = (UniqueConstraint("attempt_id", "position_id"),)

    # unique identifier of the hop
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # the id of the payment attempt
    attempt_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("payment_htlc_attempt.id"), nullable=False
    )
    attempt: Mapped[HTLCAttempt] = relationship(HTLCAttempt, back_populates="hops")

    # The position of the hop in the route. Position zero is the sender node.
    position_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Public key of the hop.
    pub_key: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # The expiry value (originally a uint32).
    expiry: Mapped[int] = mapped_column(Integer, nullable=False)

    # Amount to forward in millisatoshis.
    amt_to_forward_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Fee in millisatoshis.
    fee_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)
