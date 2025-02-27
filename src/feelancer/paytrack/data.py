import functools
from collections.abc import Iterable

from sqlalchemy import Select, func, select

from feelancer.data.db import FeelancerDB

from .models import Base, GraphNode, GraphPath, HTLCAttempt, Payment, PaymentRequest

CHUNK_SIZE = 1000
CACHE_SIZE_PAYMENT_REQUEST_ID = 1000
CACHE_SIZE_GRAPH_NODE_ID = 50000
CACHE_SIZE_GRAPH_PATH = 100000


class PaymentRequestNotFound(Exception): ...


class GraphNodeNotFound(Exception): ...


class GraphPathNotFound(Exception): ...


def query_payment_request(payment_hash: str) -> Select[tuple[PaymentRequest]]:
    qry = select(PaymentRequest).where(PaymentRequest.payment_hash == payment_hash)

    return qry


def query_max_payment_index() -> Select[tuple[int]]:
    qry = select(func.max(Payment.payment_index))
    return qry


def query_graph_node(pub_key: str) -> Select[tuple[GraphNode]]:
    qry = select(GraphNode).where(GraphNode.pub_key == pub_key)
    return qry


def query_graph_path(sha256_sum: str) -> Select[tuple[GraphPath]]:
    qry = select(GraphPath).where(GraphPath.sha256_sum == sha256_sum)
    return qry


class PaymentTrackerStore:

    def __init__(self, db: FeelancerDB) -> None:
        self.db = db
        self.db.create_base(Base)

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

        return self.db.query_first(query_max_payment_index(), lambda p: p, 0)
