import functools
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Select, desc, func, select

from feelancer.data.db import FeelancerDB
from feelancer.lightning.data import LightningStore

from .models import (
    Base,
    GraphNode,
    GraphPath,
    Hop,
    HTLCAttempt,
    Payment,
    PaymentRequest,
    Route,
)

CHUNK_SIZE = 1000
CACHE_SIZE_PAYMENT_REQUEST_ID = 1000
CACHE_SIZE_GRAPH_NODE_ID = 50000
CACHE_SIZE_GRAPH_PATH = 100000


class PaymentNotFound(Exception): ...


class PaymentRequestNotFound(Exception): ...


class GraphNodeNotFound(Exception): ...


class GraphPathNotFound(Exception): ...


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


def query_average_node_speed(
    start_time: datetime, end_time: datetime, htlc_time_cap: float
) -> Select[tuple[str, float, int]]:
    # Calculate the time difference in seconds
    time_diff = func.extract(
        "epoch", HTLCAttempt.resolve_time - HTLCAttempt.attempt_time
    )

    # Calculate the average time per route
    time_per_route = time_diff / Route.num_hops_successful

    # Limit the time per route to 60 seconds
    time_per_route = func.least(time_per_route, htlc_time_cap)

    # Query to calculate the average latency for each node
    qry = (
        select(
            GraphNode.pub_key,
            func.avg(time_per_route).label("average_speed_sec"),
            func.count(HTLCAttempt.id).label("num_attempts"),
        )
        .join(HTLCAttempt, HTLCAttempt.route_id == Route.id)
        .join(Hop, Hop.route_id == Route.id)
        .join(GraphNode, GraphNode.id == Hop.node_id)
        .filter(
            HTLCAttempt.resolve_time.between(start_time, end_time),
            Route.num_hops_successful > 0,
            Hop.position_id >= 1,
            Hop.position_id <= Route.num_hops_successful,
        )
        .group_by(GraphNode.pub_key)
        .order_by(desc("average_speed_sec"))
    )

    return qry


def query_slow_nodes(
    start_time: datetime,
    end_time: datetime,
    htlc_time_cap: float,
    min_average_speed: float,
    min_num_attempts: int,
) -> Select[tuple[str]]:
    # Subquery to calculate the average latency for each node
    subquery = query_average_node_speed(start_time, end_time, htlc_time_cap).subquery()

    # Main query to select pub_keys with average_speed_sec higher than min_average_speed
    qry = select(subquery.c.pub_key).where(
        subquery.c.average_speed_sec >= min_average_speed,
        subquery.c.num_attempts >= min_num_attempts,
    )

    return qry


class PaymentTrackerStore:

    def __init__(self, db: FeelancerDB, pubkey_local: str) -> None:
        self.db = db
        self.db.create_base(Base)

        ln_store = LightningStore(db, pubkey_local)
        self.ln_node_id = ln_store.ln_node_id

    def add_attempts(self, attempts: Iterable[HTLCAttempt]) -> None:
        """
        Adds a list of attempts to the database.
        """

        # TODO: We accept integrity errors because of this lnd issue
        # https://github.com/lightningnetwork/lnd/issues/9542
        self.db.add_all_from_iterable(attempts, True)

    def add_attempt_chunks(self, attempts: Iterable[HTLCAttempt]) -> None:
        """
        Adds a list of attempts to the database in chunks.
        """

        self.db.add_chunks_from_iterable(attempts, CHUNK_SIZE)

    def add_payment_request(self, payment_request: PaymentRequest) -> int:
        """
        Adds a payment to the database. Returns the id of the payment.
        """

        return self.db.add_post(payment_request, lambda p: p.id)

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
