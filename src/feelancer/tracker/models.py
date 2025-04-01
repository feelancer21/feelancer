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
from sqlalchemy.dialects.postgresql import JSONB
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


class PaymentRequest(Base):
    __tablename__ = "ln_payment_request"

    # unique identifier of the payment request
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # The payment hash
    payment_hash: Mapped[str] = mapped_column(
        String, nullable=False, index=True, unique=True
    )

    # The optional payment request being fulfilled
    payment_request: Mapped[str] = mapped_column(String, nullable=True)

    payments: Mapped[list[Payment]] = relationship(
        "Payment", back_populates="payment_request"
    )


class Payment(Base):
    __tablename__ = "ln_payment"
    __table_args__ = (UniqueConstraint("payment_index", "ln_node_id"),)

    # unique identifier of the payment
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # the id of the local lightning node. Not at payment request because a request
    # can theoretical be tried to be paid by multiple nodes.
    ln_node_id: Mapped[int] = mapped_column(
        ForeignKey("ln_node.id", ondelete="CASCADE"), nullable=False
    )

    # the local lightning node
    ln_node: Mapped[DBLnNode] = relationship(DBLnNode)

    payment_request_id: Mapped[int] = mapped_column(
        ForeignKey("ln_payment_request.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_request: Mapped[PaymentRequest] = relationship(
        PaymentRequest, uselist=False, back_populates="payments"
    )

    # The time in UNIX nanoseconds at which the payment was created
    creation_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    attempts: Mapped[list[HTLCAttempt]] = relationship(
        "HTLCAttempt", back_populates="payment"
    )

    resolve_info: Mapped[PaymentResolveInfo] = relationship(
        "PaymentResolveInfo", uselist=False, back_populates="payment"
    )

    # The creation index of this payment. Each payment can be uniquely identified
    # by this index, which may not strictly increment by 1 for payments made in
    # older versions of lnd.
    payment_index: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PaymentResolveInfo(Base):
    __tablename__ = "ln_payment_resolve_info"

    payment_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
        index=True,
    )

    payment: Mapped[Payment] = relationship(
        Payment, uselist=False, back_populates="resolve_info"
    )

    # The value of the payment in milli-satoshis
    value_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The fee paid for this payment in milli-satoshis
    fee_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), nullable=False)

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

    # TODO: Redesign this table to have a better unique constraint.
    # e.g. node id, payment id)
    __table_args__ = (UniqueConstraint("payment_id", "attempt_id"),)

    # unique identifier of the attempt
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    payment_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment.id", ondelete="CASCADE"), nullable=False, index=True
    )

    payment: Mapped[Payment] = relationship("Payment", back_populates="attempts")

    # The unique ID that is used for this attempt.
    attempt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The time in UNIX nanoseconds at which this HTLC was sent.
    attempt_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    route: Mapped[Route] = relationship(
        "Route", uselist=False, back_populates="htlc_attempt"
    )

    resolve_info: Mapped[PaymentHtlcResolveInfo] = relationship(
        "PaymentHtlcResolveInfo", uselist=False, back_populates="htlc_attempt"
    )


class PaymentHtlcResolveInfo(Base):
    __tablename__ = "ln_payment_htlc_resolve_info"

    htlc_attempt_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment_htlc_attempt.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    )

    htlc_attempt: Mapped[HTLCAttempt] = relationship(
        HTLCAttempt, uselist=False, back_populates="resolve_info"
    )

    # The time in UNIX nanoseconds at which this HTLC was settled or failed.
    # This field is nullable since it might not be set if the HTLC is still in flight.
    resolve_time: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The status of the HTLC.
    status: Mapped[HTLCStatus] = mapped_column(Enum(HTLCStatus), nullable=False)

    failure: Mapped[Failure] = relationship(
        "Failure", uselist=False, back_populates="htlc_attempt"
    )

    # Number of hops that were successfully reached. The sender node is not included
    # in this count.
    num_hops_successful: Mapped[int] = mapped_column(Integer, nullable=True)

    path_success_id: Mapped[int] = mapped_column(
        ForeignKey("ln_graph_path.id"), nullable=False, index=True
    )

    path_success: Mapped[GraphPath] = relationship(
        "GraphPath", foreign_keys=[path_success_id]
    )


class Failure(Base):
    __tablename__ = "ln_payment_failure"

    htlc_attempt_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment_htlc_resolve_info.htlc_attempt_id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    )
    htlc_attempt: Mapped[HTLCAttempt] = relationship(
        PaymentHtlcResolveInfo, uselist=False, back_populates="failure"
    )

    # Failure code as defined in the Lightning spec.
    code: Mapped[FailureCode] = mapped_column(Enum(FailureCode), nullable=False)

    # The position in the path of the intermediate or final node that generated
    # the failure message. Position zero is the sender node.
    source_index: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # source hops is added in an sql update after initial insert.
    # That's why nullable is True.
    source_hop_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment_hop.id"), nullable=True, index=True
    )
    source_hop: Mapped[Hop] = relationship("Hop", post_update=True)


class Route(Base):
    __tablename__ = "ln_payment_route"

    htlc_attempt_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment_htlc_attempt.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
        index=True,
    )

    htlc_attempt: Mapped[HTLCAttempt] = relationship(
        HTLCAttempt, back_populates="route"
    )

    # The cumulative (final) time lock across the entire route.
    total_time_lock: Mapped[int] = mapped_column(Integer, nullable=False)

    # The total fees in millisatoshis.
    total_fees_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The total amount in millisatoshis.
    total_amt_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Relationship to hops
    hops: Mapped[list[Hop]] = relationship("Hop", back_populates="route")

    path_id: Mapped[int] = mapped_column(
        ForeignKey("ln_graph_path.id"), nullable=False, index=True
    )

    path: Mapped[GraphPath] = relationship("GraphPath", foreign_keys=[path_id])


class Hop(Base):
    __tablename__ = "ln_payment_hop"
    __table_args__ = (UniqueConstraint("htlc_attempt_id", "position_id"),)

    # unique identifier of the hop
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # the id of the route
    htlc_attempt_id: Mapped[BigInteger] = mapped_column(
        ForeignKey("ln_payment_route.htlc_attempt_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    route: Mapped[Route] = relationship(Route, uselist=False, back_populates="hops")

    # The position of the hop in the route. Position zero is the sender node.
    position_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    node_id: Mapped[int] = mapped_column(
        ForeignKey("ln_graph_node.id"), nullable=False, index=True
    )

    node: Mapped[GraphNode] = relationship(GraphNode, foreign_keys=[node_id])

    # The expiry value (originally a uint32).
    expiry: Mapped[int] = mapped_column(Integer, nullable=False)

    # Amount to forward in millisatoshis.
    amt_to_forward_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Fee in millisatoshis.
    fee_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    node_outgoing_id: Mapped[int] = mapped_column(
        ForeignKey("ln_graph_node.id"), nullable=True, index=True
    )
    node_outgoing: Mapped[GraphNode] = relationship(
        GraphNode, foreign_keys=[node_outgoing_id]
    )

    node_incoming_id: Mapped[int] = mapped_column(
        ForeignKey("ln_graph_node.id"), nullable=True, index=True
    )
    node_incoming: Mapped[GraphNode] = relationship(
        GraphNode, foreign_keys=[node_incoming_id]
    )


class Invoice(Base):
    __tablename__ = "ln_invoice"
    __table_args__ = (UniqueConstraint("add_index", "ln_node_id"),)
    __table_args__ = (UniqueConstraint("settle_index", "ln_node_id"),)

    # unique identifier of the invoice
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    ln_node_id: Mapped[int] = mapped_column(
        ForeignKey("ln_node.id", ondelete="CASCADE"), nullable=False
    )

    # the local lightning node
    ln_node: Mapped[DBLnNode] = relationship(DBLnNode)

    # The payment hash
    r_hash: Mapped[str] = mapped_column(String, nullable=False, index=True, unique=True)

    creation_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # The value of the invoice in milli-satoshis
    value_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Relationship to invoice htlcs
    invoice_htlcs: Mapped[list[InvoiceHTLC]] = relationship(
        "InvoiceHTLC", back_populates="invoice"
    )

    add_index: Mapped[int] = mapped_column(BigInteger, nullable=False)

    settle_index: Mapped[int] = mapped_column(BigInteger, nullable=True)


class InvoiceHTLC(Base):
    __tablename__ = "ln_invoice_htlc"

    # unique identifier of the invoice
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    # The invoice id
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("ln_invoice.id", ondelete="CASCADE"), nullable=False, index=True
    )

    invoice: Mapped[Invoice] = relationship(Invoice, back_populates="invoice_htlcs")

    amt_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    accept_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    expiry_height: Mapped[int] = mapped_column(Integer, nullable=False)

    resolve_info: Mapped[InvoiceHTLCResolveInfo] = relationship(
        "InvoiceHTLCResolveInfo", uselist=False, back_populates="invoice_htlc"
    )


class InvoiceHTLCState(PyEnum):
    ACCEPTED = 0
    SETTLED = 1
    CANCELED = 2


class InvoiceHTLCResolveInfo(Base):
    __tablename__ = "ln_invoice_htlc_resolve_info"

    invoice_htlc_id: Mapped[int] = mapped_column(
        ForeignKey("ln_invoice_htlc.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
        index=True,
    )

    invoice_htlc: Mapped[InvoiceHTLC] = relationship(
        InvoiceHTLC, uselist=False, back_populates="resolve_info"
    )

    resolve_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    state: Mapped[InvoiceHTLCState] = mapped_column(
        Enum(InvoiceHTLCState), nullable=False
    )


class HtlcEventType(PyEnum):
    UNKNOWN = 0
    SEND = 1
    RECEIVE = 2
    FORWARD = 3


class HtlcDirectionType(PyEnum):
    UNKNOWN = 0
    INCOMING = 1
    OUTGOING = 2


class HtlcEvent(Base):
    __tablename__ = "ln_htlc_event"

    # Primary key
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    ln_node_id: Mapped[int] = mapped_column(
        ForeignKey("ln_node.id", ondelete="CASCADE"), nullable=False
    )

    # the local lightning node
    ln_node: Mapped[DBLnNode] = relationship(DBLnNode)

    # Incoming channel ID
    incoming_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Outgoing channel ID
    outgoing_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Incoming HTLC ID
    incoming_htlc_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Outgoing HTLC ID
    outgoing_htlc_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Human-readable timestamp derived from `timestamp_ns`
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Event type
    event_type: Mapped[HtlcEventType] = mapped_column(
        Enum(HtlcEventType), nullable=False
    )

    # JSONB fields for each event type
    forward_event: Mapped[dict] = mapped_column(JSONB, nullable=True)
    forward_fail_event: Mapped[dict] = mapped_column(JSONB, nullable=True)
    settle_event: Mapped[dict] = mapped_column(JSONB, nullable=True)
    link_fail_event: Mapped[dict] = mapped_column(JSONB, nullable=True)
    subscribed_event: Mapped[dict] = mapped_column(JSONB, nullable=True)
    final_htlc_event: Mapped[dict] = mapped_column(JSONB, nullable=True)


class Htlc(Base):
    __tablename__ = "ln_htlc"

    # Primary key
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # The htlc_index, can be NULL because lnd can only provide it for invoices at
    # the moment
    htlc_index: Mapped[int] = mapped_column(BigInteger, nullable=True)

    # The timestamp in nanoseconds since epoch when the htlc was added.
    # For payments it is the attempt time and for invoices it is the accept time.
    # For forwards it can be null for historic forwarding events.
    attempt_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=True)

    # Block height at which the HTLC expires.
    timelock: Mapped[int] = mapped_column(Integer, nullable=True)

    event_type: Mapped[HtlcEventType] = mapped_column(
        Enum(HtlcEventType), nullable=False, index=True
    )

    direction_type: Mapped[HtlcDirectionType] = mapped_column(
        Enum(HtlcDirectionType), nullable=False, index=True
    )

    # The channel ID of the incoming channel
    channel_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # The amount in millisatoshis. Nullable for forward fail events without
    # available htlc info.
    amt_msat: Mapped[int] = mapped_column(BigInteger, nullable=True)

    resolve_info: Mapped[HtlcResolveInfo] = relationship(
        "HtlcResolveInfo", uselist=False, back_populates="htlc"
    )

    __mapper_args__ = {"polymorphic_on": event_type}


class HtlcForward(Htlc):
    __tablename__ = "ln_htlc_forward"

    id: Mapped[int] = mapped_column(
        ForeignKey("ln_htlc.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    htlc: Mapped[Htlc] = relationship(Htlc, uselist=False)

    __mapper_args__ = {
        "polymorphic_identity": HtlcEventType.FORWARD,
    }


class HtlcReceive(Htlc):
    __tablename__ = "ln_htlc_receive"

    id: Mapped[int] = mapped_column(
        ForeignKey("ln_htlc.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    htlc: Mapped[Htlc] = relationship(Htlc, uselist=False)

    __mapper_args__ = {
        "polymorphic_identity": HtlcEventType.RECEIVE,
    }


class HtlcResolveType(PyEnum):
    UNKNOWN = 0
    SETTLED = 1
    LINK_FAILED = 2
    FORWARD_FAILED = 3


class HtlcResolveInfo(Base):
    __tablename__ = "ln_htlc_resolve_info"

    htlc_id: Mapped[int] = mapped_column(
        ForeignKey("ln_htlc.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
        index=True,
    )

    htlc: Mapped[Htlc] = relationship(
        Htlc, uselist=False, back_populates="resolve_info"
    )

    resolve_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # How the HTLC was resolved
    resolve_type: Mapped[HtlcResolveType] = mapped_column(
        Enum(HtlcResolveType), nullable=False
    )

    __mapper_args__ = {"polymorphic_on": resolve_type}


class HtlcResolveInfoSettled(HtlcResolveInfo):
    __tablename__ = "ln_htlc_resolve_info_settle"

    htlc_id: Mapped[int] = mapped_column(
        ForeignKey("ln_htlc_resolve_info.htlc_id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
        index=True,
    )

    resolve_info: Mapped[HtlcResolveInfo] = relationship(HtlcResolveInfo, uselist=False)

    preimage: Mapped[str] = mapped_column(String, nullable=True)

    __mapper_args__ = {"polymorphic_identity": HtlcResolveType.SETTLED}


class HtlcResolveInfoForwardFailed(HtlcResolveInfo):
    __tablename__ = "ln_htlc_resolve_info_forward_fail"

    htlc_id: Mapped[int] = mapped_column(
        ForeignKey("ln_htlc_resolve_info.htlc_id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
        index=True,
    )

    resolve_info: Mapped[HtlcResolveInfo] = relationship(HtlcResolveInfo, uselist=False)

    # Usually forward fail events occures on the outgoing channel, but can also
    # happen on the incoming channel, e.g. if a channel interceptor fails the
    # htlc.
    direction_failed: Mapped[HtlcDirectionType] = mapped_column(
        Enum(HtlcDirectionType), nullable=False
    )

    __mapper_args__ = {"polymorphic_identity": HtlcResolveType.FORWARD_FAILED}


class FailureDetail(PyEnum):
    UNKNOWN = 0
    NO_DETAIL = 1
    ONION_DECODE = 2
    LINK_NOT_ELIGIBLE = 3
    ON_CHAIN_TIMEOUT = 4
    HTLC_EXCEEDS_MAX = 5
    INSUFFICIENT_BALANCE = 6
    INCOMPLETE_FORWARD = 7
    HTLC_ADD_FAILED = 8
    FORWARDS_DISABLED = 9
    INVOICE_CANCELED = 10
    INVOICE_UNDERPAID = 11
    INVOICE_EXPIRY_TOO_SOON = 12
    INVOICE_NOT_OPEN = 13
    MPP_INVOICE_TIMEOUT = 14
    ADDRESS_MISMATCH = 15
    SET_TOTAL_MISMATCH = 16
    SET_TOTAL_TOO_LOW = 17
    SET_OVERPAID = 18
    UNKNOWN_INVOICE = 19
    INVALID_KEYSEND = 20
    MPP_IN_PROGRESS = 21
    CIRCULAR_ROUTE = 22


class HtlcResolveInfoLinkFailed(HtlcResolveInfo):
    __tablename__ = "ln_htlc_resolve_info_link_fail"

    htlc_id: Mapped[int] = mapped_column(
        ForeignKey("ln_htlc_resolve_info.htlc_id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
        index=True,
    )

    wire_failure: Mapped[FailureCode] = mapped_column(Enum(FailureCode), nullable=False)

    failure_detail: Mapped[FailureDetail] = mapped_column(
        Enum(FailureDetail), nullable=False
    )

    failure_string: Mapped[str] = mapped_column(String, nullable=True)

    resolve_info: Mapped[HtlcResolveInfo] = relationship(HtlcResolveInfo, uselist=False)

    # channel_id of the failed link. Can be different from the channel_id of the
    # htlc, e.g. we incoming htlc failed because of insufficient balance on the
    # outgoing channel.
    direction_failed: Mapped[HtlcDirectionType] = mapped_column(
        Enum(HtlcDirectionType), nullable=False
    )

    link_failed: Mapped[str] = mapped_column(String, nullable=True, index=True)

    __mapper_args__ = {"polymorphic_identity": HtlcResolveType.LINK_FAILED}


class Forward(Base):
    __tablename__ = "ln_forward"

    # Unique identifier for the forwarding event (optional, can be added if needed)
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)

    ln_node_id: Mapped[int] = mapped_column(
        ForeignKey("ln_node.id", ondelete="CASCADE"), nullable=False
    )

    # the local lightning node
    ln_node: Mapped[DBLnNode] = relationship(DBLnNode)

    # id of incoming htlc
    htlc_id_in: Mapped[int] = mapped_column(
        ForeignKey("ln_htlc_forward.id"), nullable=False, index=True
    )

    # incming htlc
    htlc_in: Mapped[Htlc] = relationship(HtlcForward, foreign_keys=[htlc_id_in])

    # id of outgoing htlc
    htlc_id_out: Mapped[int] = mapped_column(
        ForeignKey("ln_htlc_forward.id"), nullable=False, index=True
    )

    # incming htlc
    htlc_out: Mapped[Htlc] = relationship(HtlcForward, foreign_keys=[htlc_id_out])

    # The total fee (in milli-satoshis) for this payment circuit
    fee_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    resolve_info: Mapped[ForwardResolveInfo] = relationship(
        "ForwardResolveInfo", uselist=False, back_populates="forward"
    )


class ForwardResolveType(PyEnum):
    UNKNOWN = 0
    SETTLED = 1
    FAILED = 2


class ForwardResolveInfo(Base):
    __tablename__ = "ln_forward_resolve_info"

    forward_id: Mapped[int] = mapped_column(
        ForeignKey("ln_forward.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
        index=True,
    )

    forward: Mapped[Forward] = relationship(Forward, uselist=False)

    resolve_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    resolve_type: Mapped[ForwardResolveType] = mapped_column(
        Enum(ForwardResolveType), nullable=False
    )
