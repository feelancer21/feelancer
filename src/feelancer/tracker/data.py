import functools
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Delete, Float, Select, cast, delete, desc, func, select

from feelancer.data.db import FeelancerDB

from .models import (
    Base,
    Forward,
    ForwardResolveInfo,
    ForwardResolveType,
    GraphNode,
    GraphPath,
    Hop,
    Htlc,
    HtlcPayment,
    HTLCStatus,
    Invoice,
    LedgerEventHtlc,
    LedgerEventType,
    Operation,
    OperationLedgerEvent,
    OperationTransaction,
    Payment,
    PaymentHtlcResolveInfo,
    PaymentRequest,
    PaymentResolveInfo,
    PaymentStatus,
    Route,
    Transaction,
)

CACHE_SIZE_PAYMENT_REQUEST_ID = 1000
CACHE_SIZE_GRAPH_NODE_ID = 50000
CACHE_SIZE_GRAPH_PATH = 100000


class PaymentNotFound(Exception): ...


class PaymentRequestNotFound(Exception): ...


class GraphNodeNotFound(Exception): ...


class GraphPathNotFound(Exception): ...


class InvoiceNotFound(Exception): ...


def create_operation_from_htlcs(txs: list[Transaction], htlcs: list[Htlc]) -> Operation:

    op_txs = [OperationTransaction(transaction=tx) for tx in txs]
    op_events: list[OperationLedgerEvent] = []

    for htlc in htlcs:
        levent = LedgerEventHtlc(event_type=LedgerEventType.LN_HTLC_EVENT, htlc=htlc)
        op_events.append(OperationLedgerEvent(ledger_event=levent))

    return Operation(operation_transactions=op_txs, operation_ledger_events=op_events)


def query_payment_request(payment_hash: str) -> Select[tuple[PaymentRequest]]:
    qry = select(PaymentRequest).where(PaymentRequest.payment_hash == payment_hash)

    return qry


def query_payment(payment_index: int) -> Select[tuple[Payment]]:
    qry = select(Payment).where(Payment.payment_index == payment_index)
    return qry


def query_max_payment_index(ln_node_id: int) -> Select[tuple[int]]:
    qry = select(func.max(Payment.payment_index)).where(
        Payment.ln_node_id == ln_node_id
    )
    return qry


def query_graph_node(pub_key: str) -> Select[tuple[GraphNode]]:
    qry = select(GraphNode).where(GraphNode.pub_key == pub_key)
    return qry


def query_graph_path(sha256_sum: str) -> Select[tuple[GraphPath]]:
    qry = select(GraphPath).where(GraphPath.sha256_sum == sha256_sum)
    return qry


def query_invoice(r_hash: str) -> Select[tuple[Invoice]]:
    qry = select(Invoice).where(Invoice.r_hash == r_hash)
    return qry


def query_max_invoice_add_index(ln_node_id: int) -> Select[tuple[int]]:
    qry = select(func.max(Invoice.add_index)).where(Invoice.ln_node_id == ln_node_id)
    return qry


def query_count_settled_forwarding_events(ln_node_id: int) -> Select[tuple[int]]:
    qry = (
        select(func.count(Forward.id))
        .join(ForwardResolveInfo, Forward.id == ForwardResolveInfo.forward_id)
        .filter(
            Forward.ln_node_id == ln_node_id,
            ForwardResolveInfo.resolve_type == ForwardResolveType.SETTLED,
        )
    )
    return qry


def query_average_node_speed(
    start_time: datetime, end_time: datetime, percentiles: Sequence[int]
) -> tuple[Select[tuple[str, float, int]], list[str]]:
    """
    Returns a query and header to calculate the percentiles of node speed over
    the given time window.
    """

    # Calculate the time difference in seconds
    time_diff = func.extract(
        "epoch", PaymentHtlcResolveInfo.resolve_time - HtlcPayment.attempt_time
    )

    n = PaymentHtlcResolveInfo.num_hops_successful

    # Calculate the average time per route
    time_per_route = time_diff / n

    # Rationale for liquidity locked:
    # The average total liquidity locked for payments over the given time window
    # is

    # \sum_i A_i * t_i / (end_time - start_time),

    # where i is over all payments,  A_i is the amount to forward in msat and
    # t_i is resolve_time - attempt_time in seconds.

    # We'd like to break down A_i * t_i per hop in way that the sum over all
    # hops and payments keeps the same.

    # Easiest way is to assume that each hop has contributed A_i * t_i / n_i
    # with n_i being the number of hops in the route.

    # But in reality is liquidity is locked because of using  hop 1 for
    # c * n_i * t_i, because of hop 2 for c * (n_i - 1) * t_i, and so on.
    # \sum_{j=0}^{n_i-1} c * (n_i - j)  = c * n_i * (n_i + 1) / 2

    # We can solve for c by setting the sum to 1:
    # => c = 2 / (n_i * (n_i + 1))

    # Hence c * (n_i - j) is:
    # 2 * (n_i - j) / (n_i * (n_i + 1)) = 2  * (1 - j / n_i) / (n_i + 1)
    # and j = h - 1  with h=Hop.position_id

    liquidity_locked = (2 * time_diff * (1 - (Hop.position_id - 1) / n) / (n + 1)) * (
        HtlcPayment.amt_msat / (end_time - start_time).total_seconds() / 1_000
    )

    def label_percentile(p: int) -> str:
        return f"percentile_{p}_speed_sec"

    def func_percentile_time_per_route(p: int):
        label = label_percentile(p)
        return func.percentile_cont(p / 100).within_group(time_per_route).label(label)

    # Make it unique with a set and sorted
    percs = sorted(list(set(percentiles)))
    func_perc = [func_percentile_time_per_route(p) for p in percs]

    qry = (
        select(
            GraphNode.pub_key,
            *func_perc,
            cast(func.sum(liquidity_locked), Float).label("liquidity_locked_sat"),
            func.count(HtlcPayment.id).label("num_attempts"),
        )
        .select_from(PaymentHtlcResolveInfo)
        .join(HtlcPayment, PaymentHtlcResolveInfo.htlc_id == HtlcPayment.id)
        .join(Route, HtlcPayment.id == Route.htlc_id)
        .join(Hop, Hop.htlc_id == Route.htlc_id)
        .join(GraphNode, GraphNode.id == Hop.node_id)
        .filter(
            PaymentHtlcResolveInfo.resolve_time.between(start_time, end_time),
            PaymentHtlcResolveInfo.num_hops_successful > 0,
            Hop.position_id >= 1,
            Hop.position_id <= PaymentHtlcResolveInfo.num_hops_successful,
        )
        .group_by(GraphNode.pub_key)
        # First percentile of the list is used for ordering. Hence user can
        # influence the order by the config.
        .order_by(desc(label_percentile(percentiles[0])))
    )

    header = [
        "pub_key",
        *[label_percentile(p) for p in percs],
        "liquidity_locked_sat",
        "num_attempts",
    ]

    return qry, header


def query_liquidity_locked_per_htlc(
    start_time: datetime, end_time: datetime
) -> tuple[Select[tuple[int, datetime, datetime | None, int, float]], list[str]]:
    """
    Calculates the locked liquidity per HTLC over the given time window.
    """

    # Calculate the time difference in seconds
    time_diff = func.extract(
        "epoch", PaymentHtlcResolveInfo.resolve_time - HtlcPayment.attempt_time
    )

    liquidity_locked = (
        time_diff * HtlcPayment.amt_msat / (end_time - start_time).total_seconds()
    ) / 1_000

    qry = (
        select(
            HtlcPayment.attempt_id,
            HtlcPayment.attempt_time,
            PaymentHtlcResolveInfo.resolve_time,
            time_diff,
            cast(liquidity_locked, Float).label("liquidity_locked_sat"),
        )
        .select_from(PaymentHtlcResolveInfo)
        .join(HtlcPayment, PaymentHtlcResolveInfo.htlc_id == HtlcPayment.id)
        .join(Route, HtlcPayment.id == Route.htlc_id)
        .filter(
            PaymentHtlcResolveInfo.resolve_time.between(start_time, end_time),
            PaymentHtlcResolveInfo.num_hops_successful > 0,
        )
        .order_by(desc("liquidity_locked_sat"))
    )
    header = [
        "attempt_id",
        "attempt_time",
        "resolve_time",
        "time_diff",
        "liquidity_locked_sat",
    ]
    return qry, header


def query_slow_nodes(
    start_time: datetime,
    end_time: datetime,
    percentile: int,
    min_speed: float,
    min_num_attempts: int,
) -> Select[tuple[str]]:
    # Subquery to calculate the median latency for each node

    res = query_average_node_speed(start_time, end_time, [percentile])
    subquery = res[0].subquery()

    # Main query to select pub_keys with a speed higher than min_speed
    qry = select(subquery.c.pub_key).where(
        list(subquery.c)[1] >= min_speed,
        subquery.c.num_attempts >= min_num_attempts,
    )

    return qry


def delete_failed_htlc_attempts(
    deletion_cutoff: datetime,
) -> Delete[tuple[HtlcPayment]]:
    """
    Returns a query to delete all failed HTLC attempts that are older than the
    given time. Time is exclusive.
    """

    return delete(HtlcPayment).where(
        Htlc.id == HtlcPayment.htlc_id,
        HtlcPayment.htlc_id == PaymentHtlcResolveInfo.htlc_id,
        HtlcPayment.attempt_time < deletion_cutoff,
        PaymentHtlcResolveInfo.status == HTLCStatus.FAILED,
    )


def delete_failed_payments(deletion_cutoff: datetime) -> Delete[tuple[Payment]]:
    """
    Returns a query to delete all failed payments that are older than the given
    time. Time is exclusive.
    """

    return delete(Payment).where(
        Transaction.id == Payment.tx_id,
        Payment.tx_id == PaymentResolveInfo.payment_id,
        Payment.creation_time < deletion_cutoff,
        PaymentResolveInfo.status == PaymentStatus.FAILED,
    )


def delete_orphaned_payment_requests() -> Delete[tuple[int]]:
    """
    Returns a query to delete all orphaned payment requests.
    """

    return delete(PaymentRequest).where(~PaymentRequest.payments.any())


class TrackerStore:

    def __init__(self, db: FeelancerDB, ln_node_id: int) -> None:
        self.db = db
        self.db.create_base(Base)

        self.ln_node_id = ln_node_id

    def add_graph_node(self, pub_key: str) -> int:
        """
        Adds a graph node to the database. Returns the id of the graph node.
        """

        return self.db.add_post(GraphNode(pub_key=pub_key), lambda p: p.id)

    def add_graph_path(self, path: GraphPath) -> int:
        """
        Adds a graph path to the database. Returns the id of the graph path.
        """

        return self.db.add_post(path, lambda p: p.id)

    def delete_orphaned_payments(self) -> None:
        """
        Deletes all orphaned objects of this store. Atm only orphaned payment
        requests are deleted.
        """

        # TODO: Delete orphaned graph paths and graph nodes
        self.db.core_delete(delete_orphaned_payment_requests())

    def get_payment_id(self, payment_index: int) -> int:
        """
        Returns the payment id for a given payment index.
        """

        id = self.db.query_first(query_payment(payment_index), lambda p: p.id)
        if id is None:
            raise PaymentNotFound(f"Payment with index {payment_index} not found.")
        return id

    @functools.lru_cache(maxsize=CACHE_SIZE_PAYMENT_REQUEST_ID)
    def get_payment_request_id(self, payment_hash: str) -> int:
        """
        Returns the payment id for a given payment hash.
        """

        id = self.db.query_first(query_payment_request(payment_hash), lambda p: p.id)
        if id is None:
            raise PaymentRequestNotFound(
                f"Payment request with hash {payment_hash} not found."
            )
        return id

    @functools.lru_cache(maxsize=CACHE_SIZE_GRAPH_NODE_ID)
    def get_graph_node_id(self, pub_key: str) -> int:
        """
        Returns the graph node id for a given pub key.
        """

        id = self.db.query_first(query_graph_node(pub_key), lambda p: p.id)
        if id is None:
            raise GraphNodeNotFound(f"Graph node with key {pub_key} not found.")
        return id

    @functools.lru_cache(maxsize=CACHE_SIZE_GRAPH_PATH)
    def get_graph_path_id(self, sha_256_sum: str) -> int:
        """
        Returns the graph path id for a given sha256 sum.
        """

        id = self.db.query_first(query_graph_path(sha_256_sum), lambda p: p.id)
        if id is None:
            raise GraphPathNotFound(
                f"Graph path with sha256 sum {sha_256_sum} not found."
            )
        return id

    def get_max_payment_index(self) -> int:
        """
        Returns the maximum payment index.
        """

        return self.db.query_first(
            query_max_payment_index(self.ln_node_id), lambda p: p, 0
        )

    def get_invoice_id(self, r_hash: str) -> int:
        """
        Returns the invoice for a given r_hash.
        """

        id = self.db.query_first(query_invoice(r_hash), lambda p: p.id)
        if id is None:
            raise InvoiceNotFound(f"Invoice with r_hash {r_hash} not found.")
        return id

    def get_max_invoice_add_index(self) -> int:
        """
        Returns the maximum invoice add index.
        """

        return self.db.query_first(
            query_max_invoice_add_index(self.ln_node_id), lambda p: p, 0
        )

    def get_count_forwarding_events(self) -> int:
        """
        Returns the count of forwarding events.
        """

        return self.db.query_first(
            query_count_settled_forwarding_events(self.ln_node_id), lambda p: p, 0
        )
