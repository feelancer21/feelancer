import functools
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from sqlalchemy import Delete, Float, Select, cast, delete, desc, func, select

from feelancer.data.db import FeelancerDB

from .models import (
    Base,
    Forward,
    GraphNode,
    GraphPath,
    Hop,
    Htlc,
    HtlcEvent,
    HtlcPayment,
    HtlcResolveInfo,
    HtlcResolveInfoPayment,
    HtlcResolveType,
    Invoice,
    LedgerEventHtlc,
    LedgerEventType,
    Operation,
    OperationLedgerEvent,
    OperationTransaction,
    Payment,
    Route,
    Transaction,
    TransactionResolveInfo,
    TransactionResolveType,
)

CACHE_SIZE_PAYMENT_REQUEST_ID = 1000
CACHE_SIZE_PAYMENT_ID = 100000
CACHE_SIZE_INVOICE_ID = 100000
CACHE_SIZE_GRAPH_NODE_ID = 50000
CACHE_SIZE_GRAPH_PATH = 100000


def new_operation_from_htlcs(
    txs: list[Transaction], htlcs: Sequence[Htlc]
) -> Operation:

    op_txs = [OperationTransaction(transaction=tx) for tx in txs]
    op_events: list[OperationLedgerEvent] = []

    for htlc in htlcs:
        # We only create a ledger event for settled htlcs
        if htlc.resolve_info is None:
            continue

        if htlc.resolve_info.resolve_type != HtlcResolveType.SETTLED:
            continue

        levent = LedgerEventHtlc(event_type=LedgerEventType.LN_HTLC_EVENT, htlc=htlc)
        op_events.append(OperationLedgerEvent(ledger_event=levent))

    return Operation(operation_transactions=op_txs, operation_ledger_events=op_events)


def query_payment(ln_node_id: int, payment_index: int) -> Select[tuple[Payment]]:
    qry = select(Payment).where(
        Payment.payment_index == payment_index, Payment.ln_node_id == ln_node_id
    )
    return qry


def query_max_payment_index(ln_node_id: int) -> Select[tuple[int]]:
    qry = select(func.max(Payment.payment_index)).where(
        Payment.ln_node_id == ln_node_id
    )
    return qry


def query_graph_node(pub_key: str) -> Select[tuple[GraphNode]]:
    qry = select(GraphNode).where(GraphNode.pub_key == pub_key)
    return qry


def query_graph_path(node_ids: Iterable[int]) -> Select[tuple[GraphPath]]:
    qry = select(GraphPath).where(GraphPath.node_ids == node_ids)
    return qry


def query_invoice(ln_node_id: int, add_index: int) -> Select[tuple[Invoice]]:
    qry = select(Invoice).where(
        Invoice.ln_node_id == ln_node_id, Invoice.add_index == add_index
    )
    return qry


def query_max_invoice_add_index(ln_node_id: int) -> Select[tuple[int]]:
    qry = select(func.max(Invoice.add_index)).where(Invoice.ln_node_id == ln_node_id)
    return qry


def query_count_settled_forwarding_events(ln_node_id: int) -> Select[tuple[int]]:
    qry = (
        select(func.count(Forward.id))
        .join(TransactionResolveInfo, Forward.id == TransactionResolveInfo.tx_id)
        .filter(
            Forward.ln_node_id == ln_node_id,
            TransactionResolveInfo.resolve_type == TransactionResolveType.SETTLED,
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
        "epoch", HtlcResolveInfo.resolve_time - HtlcPayment.attempt_time
    )

    n = HtlcResolveInfoPayment.num_hops_successful

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
        .select_from(HtlcResolveInfo)
        .join(
            HtlcResolveInfoPayment,
            HtlcResolveInfo.htlc_id == HtlcResolveInfoPayment.htlc_id,
        )
        .join(HtlcPayment, HtlcResolveInfoPayment.htlc_id == HtlcPayment.id)
        .join(Route, HtlcPayment.id == Route.htlc_id)
        .join(Hop, Hop.htlc_id == Route.htlc_id)
        .join(GraphNode, GraphNode.id == Hop.node_id)
        .filter(
            HtlcResolveInfo.resolve_time.between(start_time, end_time),
            HtlcResolveInfoPayment.num_hops_successful > 0,
            Hop.position_id >= 1,
            Hop.position_id <= HtlcResolveInfoPayment.num_hops_successful,
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
) -> tuple[Select[tuple[int, datetime, datetime, int, float]], list[str]]:
    """
    Calculates the locked liquidity per HTLC over the given time window.
    """

    # Calculate the time difference in seconds
    time_diff = func.extract(
        "epoch", HtlcResolveInfo.resolve_time - HtlcPayment.attempt_time
    )

    liquidity_locked = (
        time_diff * HtlcPayment.amt_msat / (end_time - start_time).total_seconds()
    ) / 1_000

    qry = (
        select(
            HtlcPayment.attempt_id,
            HtlcPayment.attempt_time,
            HtlcResolveInfo.resolve_time,
            time_diff,
            cast(liquidity_locked, Float).label("liquidity_locked_sat"),
        )
        .select_from(HtlcResolveInfo)
        .join(
            HtlcResolveInfoPayment,
            HtlcResolveInfo.htlc_id == HtlcResolveInfoPayment.htlc_id,
        )
        .join(HtlcPayment, HtlcResolveInfoPayment.htlc_id == HtlcPayment.id)
        .join(Route, HtlcPayment.id == Route.htlc_id)
        .filter(
            HtlcResolveInfo.resolve_time.between(start_time, end_time),
            HtlcResolveInfoPayment.num_hops_successful > 0,
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


def delete_failed_htlcs(
    deletion_cutoff: datetime,
) -> Delete[tuple[Htlc]]:
    """
    Returns a query to delete all failed HTLC attempts that are older than the
    given time. Time is exclusive.
    """

    return delete(Htlc).where(
        Htlc.id == HtlcResolveInfo.htlc_id,
        Htlc.attempt_time < deletion_cutoff,
        HtlcResolveInfo.resolve_type.in_(
            [
                HtlcResolveType.PAYMENT_FAILED,
                HtlcResolveType.FORWARD_FAILED,
                HtlcResolveType.LINK_FAILED,
            ]
        ),
    )


def delete_failed_transactions(deletion_cutoff: datetime) -> Delete[tuple[Transaction]]:
    """
    Returns a query to delete all failed payments that are older than the given
    time. Time is exclusive.
    """

    return delete(Transaction).where(
        Transaction.id == TransactionResolveInfo.tx_id,
        Transaction.creation_time < deletion_cutoff,
        TransactionResolveInfo.resolve_type.in_([TransactionResolveType.FAILED]),
    )


def delete_htlc_events(deletion_cutoff: datetime) -> Delete[tuple[HtlcEvent]]:
    """
    Returns a query to delete all HTLC events that are older than the given time.
    Time is exclusive.
    """

    return delete(HtlcEvent).where(HtlcEvent.timestamp < deletion_cutoff)


def delete_orphaned_operations() -> Delete[tuple[Operation]]:
    """
    Returns a query to delete all orphaned operations that are not linked to
    any operation transactions.
    """

    return delete(Operation).where(~Operation.operation_transactions.any())


class TrackerStore:

    def __init__(self, db: FeelancerDB, ln_node_id: int) -> None:
        self.db = db
        self.db.new_base(Base)
        self.ln_node_id = ln_node_id

        self._get_graph_node_id_or_add = self.db.new_get_id_or_add(
            get_qry=query_graph_node,
            read_id=lambda p: p.id,
        )

        self._get_graph_path_id_or_add = self.db.new_get_id_or_add(
            get_qry=query_graph_path,
            read_id=lambda p: p.id,
        )

        self._get_invoice_id_or_add: Callable[[int, None], int] = (
            self.db.new_get_id_or_add(
                get_qry=lambda index: query_invoice(self.ln_node_id, index),
                read_id=lambda p: p.id,
            )
        )

        self._get_payment_id_or_add: Callable[[int, None], int] = (
            self.db.new_get_id_or_add(
                get_qry=lambda index: query_payment(self.ln_node_id, index),
                read_id=lambda p: p.id,
            )
        )

    def delete_orphaned_payments(self) -> None:
        """
        Deletes all orphaned objects of this store.
        """

        # Not needed at the moment
        return None

    @functools.lru_cache(maxsize=CACHE_SIZE_GRAPH_NODE_ID)
    def get_graph_node_id(self, pub_key: str) -> int:
        """
        Returns the id of the graph node for a given pub key. If the node does
        not exist, it is added to the database.
        """

        return self._get_graph_node_id_or_add(
            pub_key, lambda: GraphNode(pub_key=pub_key)
        )

    @functools.lru_cache(maxsize=CACHE_SIZE_GRAPH_PATH)
    def get_graph_path_id(self, node_ids: tuple[int, ...]) -> int:
        """
        Returns the id of the graph path for a given tuple of node ids. If the
        path does not exist, it is added to the database.
        """

        return self._get_graph_path_id_or_add(
            node_ids, lambda: GraphPath(node_ids=node_ids)
        )

    @functools.lru_cache(maxsize=CACHE_SIZE_PAYMENT_ID)
    def get_payment_id(self, payment_index: int) -> int:
        """
        Returns the tx id for a given payment index.
        """

        return self._get_payment_id_or_add(payment_index, None)

    @functools.lru_cache(maxsize=CACHE_SIZE_INVOICE_ID)
    def get_invoice_id(self, add_index: int) -> int:
        """
        Returns the tx id for a given add_index.
        """

        return self._get_invoice_id_or_add(add_index, None)

    def get_max_invoice_add_index(self) -> int:
        """
        Returns the maximum invoice add index.
        """

        return self.db.sel_first(
            query_max_invoice_add_index(self.ln_node_id), lambda p: p, 0
        )

    def get_max_payment_index(self) -> int:
        """
        Returns the maximum payment index.
        """

        return self.db.sel_first(
            query_max_payment_index(self.ln_node_id), lambda p: p, 0
        )

    def get_count_forwarding_events(self) -> int:
        """
        Returns the count of forwarding events.
        """

        return self.db.sel_first(
            query_count_settled_forwarding_events(self.ln_node_id), lambda p: p, 0
        )
