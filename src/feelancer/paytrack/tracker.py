import datetime
import hashlib
import logging
from collections.abc import Generator, Iterable
from typing import Protocol

import pytz
from google.protobuf.json_format import MessageToDict

from feelancer.data.db import FeelancerDB
from feelancer.lightning.data import LightningStore
from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.client import LndGrpc
from feelancer.lnd.grpc_generated import lightning_pb2 as ln

from .data import (
    GraphNodeNotFound,
    GraphPathNotFound,
    PaymentNotFound,
    PaymentTrackerStore,
)
from .models import (
    Failure,
    FailureCode,
    GraphPath,
    Hop,
    HTLCAttempt,
    HTLCStatus,
    Payment,
    PaymentFailureReason,
    PaymentStatus,
    Route,
)


# Helper function for converting nanoseconds to a datetime object.
def _ns_to_datetime(ns: int) -> datetime.datetime:
    """Convert UNIX nanoseconds to a timezone-aware datetime (UTC)."""
    return datetime.datetime.fromtimestamp(ns / 1e9, tz=pytz.utc)


def _sha256_path(path: Iterable[int]) -> str:
    """Creates the sha256sum of the concatenation of all public keys."""

    # concat all the ids
    string = ",".join([str(h) for h in path])
    return hashlib.sha256(string.encode("utf-8")).hexdigest()


def _sha256_payment(payment: ln.Payment | ln.HTLCAttempt) -> str:
    """Creates the sha256sum of the payment object."""
    return hashlib.sha256(payment.SerializeToString(deterministic=True)).hexdigest()


def _convert_failure(failure: ln.Failure, source_hop: Hop | None) -> Failure:
    return Failure(
        code=FailureCode(failure.code),
        source_index=failure.failure_source_index,
        source_hop=source_hop,
    )


class PaymentTracker(Protocol):

    def store_payments(self) -> None: ...

    """
    Stores payments in the database.
    """


class LNDPaymentTracker:

    def __init__(self, lnd: LndGrpc, db: FeelancerDB):

        self._lnd = LNDClient(lnd)
        self._store = PaymentTrackerStore(db)
        self._pub_key = self._lnd.pubkey_local
        self._ln_store = LightningStore(db, self._pub_key)
        self._ln_node_id = self._ln_store.ln_node_id

    def store_payments(self) -> None:

        self._store.add_attempts(self._generate_attempts())

    def _generate_attempts(self) -> Generator[HTLCAttempt]:

        dispatcher = self._lnd.lnd.track_payments_dispatcher
        for s in dispatcher.subscribe(self._process_payment):
            yield from s

    def _process_payment(self, p: ln.Payment) -> Generator[HTLCAttempt]:
        """
        Callback function for the subscription. Converts the payment object
        to an Iterable of HTLCAttempt objects.
        """

        # only process status SUCCEEDED or FAILED
        if p.status not in [2, 3]:
            return

        payment_id: int

        # Check if we have already stored the payment in the database.
        # Maybe from the last run.
        try:
            payment_id = self._store.get_payment_id(p.payment_hash)
        except PaymentNotFound as e:
            logging.warning(e)
            # If found not we store it and get the id of the payment.
            payment_id = self._store.add_payment(self._convert_payment(p))

        logging.debug(f"payment {p.payment_hash=}:\n {MessageToDict(p)}")
        for h in p.htlcs:
            logging.debug(
                f"payment {p.payment_hash=} {p.status=} {h.attempt_id=} {h.status=} {_sha256_payment(p)=} {_sha256_payment(h)=}"
            )
            yield self._convert_htlc_attempt(h, payment_id)

    def _convert_payment(self, payment: ln.Payment) -> Payment:
        """
        Converts a payment object from the LND gRPC API to a Payment
        """

        return Payment(
            payment_hash=payment.payment_hash,
            payment_preimage=payment.payment_preimage,
            value_msat=payment.value_msat,
            status=PaymentStatus(payment.status),
            creation_time=_ns_to_datetime(payment.creation_time_ns),
            fee_msat=payment.fee_msat,
            payment_index=payment.payment_index,
            failure_reason=PaymentFailureReason(payment.failure_reason),
        )

    def _convert_htlc_attempt(self, attempt: ln.HTLCAttempt, payment_id) -> HTLCAttempt:

        if attempt.resolve_time_ns > 0:
            resolve_time = _ns_to_datetime(attempt.resolve_time_ns)
        else:
            resolve_time = None

        if attempt.route is not None:
            failure_source_index = attempt.failure.failure_source_index
        else:
            failure_source_index = None

        route = self._convert_route(attempt.route, failure_source_index)

        if attempt.failure is not None:
            try:
                source_hop = route.hops[attempt.failure.failure_source_index]
            except IndexError:
                source_hop = None
                logging.warning(
                    f"Failure source index out of bounds: {attempt.failure.failure_source_index=}, ",
                    f"{attempt.attempt_id=}",
                )
            failure = _convert_failure(attempt.failure, source_hop)
        else:
            failure = None

        return HTLCAttempt(
            ln_node_id=self._ln_node_id,
            payment_id=payment_id,
            attempt_id=attempt.attempt_id,
            status=HTLCStatus(attempt.status),
            attempt_time=_ns_to_datetime(attempt.attempt_time_ns),
            resolve_time=resolve_time,
            route=route,
            failure=failure,
        )

    def _convert_route(
        self, route: ln.Route, failure_source_index: int | None
    ) -> Route:

        hops: list[Hop] = []
        path: list[int] = []

        # For data analysis we want to store the first hop as a separate entry.
        node_id = self._get_graph_node_id(self._pub_key)
        hop_orm = Hop(
            position_id=0,
            expiry=route.total_time_lock,
            amt_to_forward_msat=route.total_amt_msat,
            fee_msat=0,
            node_id=node_id,
            node_outgoing_id=None,
            node_incoming_id=None,
        )
        hops.append(hop_orm)

        for i, hop in enumerate(route.hops):
            last_node_id = node_id
            node_id = self._get_graph_node_id(hop.pub_key)
            path.append(node_id)

            hop_orm.node_outgoing_id = node_id
            hop_orm = Hop(
                position_id=i + 1,
                expiry=hop.expiry,
                amt_to_forward_msat=hop.amt_to_forward_msat,
                fee_msat=hop.fee_msat,
                node_id=node_id,
                node_outgoing_id=None,
                node_incoming_id=last_node_id,
            )
            hops.append(hop_orm)

        path_id = self._get_graph_path_id(path)

        if failure_source_index is not None and failure_source_index >= 0:
            path_success = path[:failure_source_index]
            path_success_id = self._get_graph_path_id(path_success)
        else:
            path_success_id = None

        return Route(
            total_time_lock=route.total_time_lock,
            total_amt_msat=route.total_amt_msat,
            total_fees_msat=route.total_fees_msat,
            first_hop_amount_msat=route.first_hop_amount_msat,
            hops=hops,
            path_id=path_id,
            path_success_id=path_success_id,
        )

    def _get_graph_node_id(self, pub_key: str) -> int:
        """
        Returns the id of a graph node. If not found it will be added to the database.
        """

        try:
            return self._store.get_graph_node_id(pub_key)
        except GraphNodeNotFound:
            return self._store.add_graph_node(pub_key)

    def _get_graph_path_id(self, path: list[int]) -> int:
        sha_path = _sha256_path(path)
        try:
            path_id = self._store.get_graph_path_id(sha_path)
        except GraphPathNotFound:
            path_id = self._store.add_graph_path(
                GraphPath(sha256_sum=sha_path, node_ids=path)
            )
        return path_id
