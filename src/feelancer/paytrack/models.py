"""
Database Model for PaymentTracker. Inspired by lnd payment protos.
"""

from __future__ import annotations

import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    ARRAY,
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


class PaymentFailureReason(PyEnum):
    FAILURE_REASON_NONE = 0
    FAILURE_REASON_TIMEOUT = 1
    FAILURE_REASON_NO_ROUTE = 2
    FAILURE_REASON_ERROR = 3
    FAILURE_REASON_INCORRECT_PAYMENT_DETAILS = 4
    FAILURE_REASON_INSUFFICIENT_BALANCE = 5
    FAILURE_REASON_CANCELED = 6


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


class PaymentStatus(PyEnum):
    IN_FLIGHT = 1
    SUCCEEDED = 2
    FAILED = 3
    INITIATED = 4


class Payment(Base):
    __tablename__ = "ln_payment"

    # unique identifier of the payment
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # The payment hash
    payment_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # The payment preimage
    payment_preimage: Mapped[str] = mapped_column(String, nullable=True)

    # The value of the payment in milli-satoshis
    value_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The optional payment request being fulfilled
    payment_request: Mapped[str] = mapped_column(String, nullable=True)

    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), nullable=False)

    # The fee paid for this payment in milli-satoshis
    fee_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The time in UNIX nanoseconds at which the payment was created
    creation_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    attempts: Mapped[list[HTLCAttempt]] = relationship(
        "HTLCAttempt", back_populates="payment"
    )

    # The creation index of this payment. Each payment can be uniquely identified
    # by this index, which may not strictly increment by 1 for payments made in
    # older versions of lnd.
    payment_index: Mapped[int] = mapped_column(BigInteger, nullable=False)

    failure_reason: Mapped[PaymentFailureReason] = mapped_column(
        Enum(PaymentFailureReason), nullable=False
    )


class GraphNode(Base):
    __tablename__ = "ln_graph_node"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    pub_key: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )


class GraphPath(Base):
    __tablename__ = "ln_graph_path"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # The sha256sum of the concatenation of all ids separated by a comma.
    sha256_sum: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    # Use the ARRAY type to store a variable-length list of node ids
    node_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), unique=True, nullable=False
    )


class HTLCAttempt(Base):
    __tablename__ = "ln_payment_htlc_attempt"
    __table_args__ = (UniqueConstraint("ln_node_id", "attempt_id", "status"),)

    # unique identifier of the attempt
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # the id of the local lightning node
    ln_node_id: Mapped[int] = mapped_column(ForeignKey("ln_node.id"), nullable=False)

    # the local lightning node
    ln_node: Mapped[DBLnNode] = relationship(DBLnNode)

    payment_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment.id"), nullable=True
    )

    payment: Mapped[Payment] = relationship("Payment", back_populates="attempts")

    # The unique ID that is used for this attempt.
    attempt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The status of the HTLC.
    status: Mapped[HTLCStatus] = mapped_column(Enum(HTLCStatus), nullable=False)

    # The time in UNIX nanoseconds at which this HTLC was sent.
    attempt_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # The time in UNIX nanoseconds at which this HTLC was settled or failed.
    # This field is nullable since it might not be set if the HTLC is still in flight.
    resolve_time: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    route_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment_route.id"), nullable=False
    )
    route: Mapped[Route] = relationship("Route", back_populates="attempt")

    failure_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment_failure.id"), nullable=True
    )
    failure: Mapped[Failure] = relationship("Failure")


class Failure(Base):
    __tablename__ = "ln_payment_failure"

    # unique identifier of the failure
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # Failure code as defined in the Lightning spec.
    code: Mapped[FailureCode] = mapped_column(Enum(FailureCode), nullable=False)

    # The position in the path of the intermediate or final node that generated
    # the failure message. Position zero is the sender node.
    source_index: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # source hops is added in an sql update after initial insert.
    # That's why nullable is True.
    source_hop_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment_hop.id"), nullable=True
    )
    source_hop: Mapped[Hop] = relationship("Hop", post_update=True)


class Route(Base):
    __tablename__ = "ln_payment_route"

    # unique identifier of the route
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # The cumulative (final) time lock across the entire route.
    total_time_lock: Mapped[int] = mapped_column(Integer, nullable=False)

    # The total fees in millisatoshis.
    total_fees_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The total amount in millisatoshis.
    total_amt_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The payment attempt
    attempt: Mapped[HTLCAttempt] = relationship("HTLCAttempt", back_populates="route")

    # Relationship to hops
    hops: Mapped[list[Hop]] = relationship("Hop", back_populates="route")

    path_id: Mapped[int] = mapped_column(ForeignKey("ln_graph_path.id"), nullable=False)

    path: Mapped[GraphPath] = relationship("GraphPath", foreign_keys=[path_id])

    path_success_id: Mapped[int] = mapped_column(
        ForeignKey("ln_graph_path.id"), nullable=True
    )

    path_success: Mapped[GraphPath] = relationship(
        "GraphPath", foreign_keys=[path_success_id]
    )


class Hop(Base):
    __tablename__ = "ln_payment_hop"
    __table_args__ = (UniqueConstraint("route_id", "position_id"),)

    # unique identifier of the hop
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # the id of the route
    route_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment_route.id"), nullable=False
    )
    route: Mapped[Route] = relationship(Route, back_populates="hops")

    # The position of the hop in the route. Position zero is the sender node.
    position_id: Mapped[int] = mapped_column(Integer, nullable=False)

    node_id: Mapped[int] = mapped_column(ForeignKey("ln_graph_node.id"), nullable=False)

    node: Mapped[GraphNode] = relationship(GraphNode, foreign_keys=[node_id])

    # The expiry value (originally a uint32).
    expiry: Mapped[int] = mapped_column(Integer, nullable=False)

    # Amount to forward in millisatoshis.
    amt_to_forward_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Fee in millisatoshis.
    fee_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    node_outgoing_id: Mapped[int] = mapped_column(
        ForeignKey("ln_graph_node.id"), nullable=True
    )
    node_outgoing: Mapped[GraphNode] = relationship(
        GraphNode, foreign_keys=[node_outgoing_id]
    )

    node_incoming_id: Mapped[int] = mapped_column(
        ForeignKey("ln_graph_node.id"), nullable=True
    )
    node_incoming: Mapped[GraphNode] = relationship(
        GraphNode, foreign_keys=[node_incoming_id]
    )
