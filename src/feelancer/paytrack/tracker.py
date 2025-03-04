import datetime
import functools
import hashlib
import logging
from collections.abc import Callable, Generator, Iterable
from typing import Any, Protocol, TypeVar

import pytz
from google.protobuf.json_format import MessageToDict

from feelancer.base import default_retry_handler
from feelancer.data.db import FeelancerDB
from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.client import LndGrpc
from feelancer.lnd.grpc_generated import lightning_pb2 as ln

from .data import (
    GraphNodeNotFound,
    GraphPathNotFound,
    PaymentNotFound,
    PaymentRequestNotFound,
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
    PaymentRequest,
    PaymentStatus,
    Route,
)

T = TypeVar("T")

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds


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


def _convert_failure(
    failure: ln.Failure, attempt: HTLCAttempt, source_hop: Hop | None
) -> Failure:
    return Failure(
        code=FailureCode(failure.code),
        source_index=failure.failure_source_index,
        source_hop=source_hop,
        htlc_attempt=attempt,
    )


class PaymentTracker(Protocol):

    def store_payments(self) -> None: ...

    """
    Stores the payments in the database.
    """

    def pre_sync_start(self) -> None: ...
    def pre_sync_stop(self) -> None: ...


def _create_yield_logger(
    interval: int,
) -> Callable[[Callable[..., Generator[T]]], Callable[..., Generator[T]]]:
    """
    Decorator for writing a log message in the given interval of yielded items.
    To see the process is still alive.
    """

    def decorator(
        generator_func: Callable[..., Generator[T]],
    ) -> Callable[..., Generator[T]]:

        @functools.wraps(generator_func)
        def wrapper(*args: Any, **kwargs: Any) -> Generator[T]:
            count: int = 0
            for item in generator_func(*args, **kwargs):
                count += 1
                if count == interval:
                    logging.info(f"Processed {count} payments")
                    count = 0
                yield item

            logging.info(f"Processed {count} payments")

        return wrapper

    return decorator


class LNDPaymentTracker:

    def __init__(self, lnd: LndGrpc, db: FeelancerDB):

        self._lnd = LNDClient(lnd)
        self._pub_key = self._lnd.pubkey_local
        self._store = PaymentTrackerStore(db, self._pub_key)
        self._is_stopped = False

    def store_payments(self) -> None:
        self._store.add_attempts(self._attempts_from_stream())

    def pre_sync_start(self) -> None:
        """
        Presync payments from the LND ListPayments API. This is done before the
        subscription starts. This is necessary to get the payments that were
        made while the subscription was not running.
        """

        logging.info(f"Presync payments for {self._pub_key}...")

        self._pre_sync_start()

        logging.info(f"Presync payments for {self._pub_key} finished")

    def pre_sync_stop(self) -> None:
        """
        Stops the presync process.
        """
        self._is_stopped = True

    @default_retry_handler
    def _pre_sync_start(self) -> None:
        """
        Starts the presync process with a retry handler.
        """

        if self._is_stopped:
            return
        self._store.add_attempt_chunks(self._attempts_from_paginator())

    @_create_yield_logger(interval=1000)
    def _attempts_from_paginator(self) -> Generator[HTLCAttempt]:

        index_offset = self._store.get_max_payment_index()
        logging.debug(f"Starting from index {index_offset} for {self._pub_key}")

        generator = self._lnd.lnd.paginate_payments(
            index_offset=index_offset, include_incomplete=True
        )
        for payment in generator:
            if self._is_stopped:
                generator.close()

            yield from self._process_payment(payment, False)

    @_create_yield_logger(interval=100)
    def _attempts_from_stream(self) -> Generator[HTLCAttempt]:

        # get_recon_source returns a new paginator for ListPayments over
        # the last 30 days.
        def get_recon_source() -> Generator[ln.Payment]:
            recon_start = datetime.datetime.now(tz=pytz.utc) - datetime.timedelta(
                seconds=RECON_TIME_INTERVAL
            )
            return self._lnd.lnd.paginate_payments(
                include_incomplete=True,
                creation_date_start=int(recon_start.timestamp()),
            )

        dispatcher = self._lnd.lnd.track_payments_dispatcher
        for s in dispatcher.subscribe(self._process_payment, get_recon_source):
            # each s is a generator of the htlc attempt for one payment
            yield from s

    def _process_payment(
        self, p: ln.Payment, recon_running: bool
    ) -> Generator[HTLCAttempt]:
        """
        Callback function for the subscription. Converts the payment object
        to an Iterable of HTLCAttempt objects.
        """

        # logging.debug(f"Processing payment: {recon_running=} {MessageToDict(p)=}")

        # only process status SUCCEEDED or FAILED
        if p.status not in [2, 3]:
            return

        # If we are in the reconciliation process we check if the payment is already
        # stored in the database. If so we skip it.
        # Outside of the reconciliation process this check is not necessary, because
        # we can assume that the payment is not stored in the database.
        if recon_running:
            try:
                self._store.get_payment_id(p.payment_index)
                return
            except PaymentNotFound:
                logging.debug(f"Payment reconciliation: {p.payment_index=} not found.")
                pass

        payment_request_id: int

        # Check if we have already stored the payment in the database.
        # Maybe from the last run.
        try:
            payment_request_id = self._store.get_payment_request_id(p.payment_hash)
        except PaymentRequestNotFound:
            pay_req = PaymentRequest(
                payment_hash=p.payment_hash,
                payment_request=p.payment_request,
            )
            # If not found, we store it and get the id of the payment.
            payment_request_id = self._store.add_payment_request(pay_req)

        payment = self._convert_payment(p, payment_request_id)

        for h in p.htlcs:
            yield self._convert_htlc_attempt(h, payment)

    def _convert_payment(self, payment: ln.Payment, payment_request_id: int) -> Payment:
        """
        Converts a payment object from the LND gRPC API to a Payment
        """

        return Payment(
            payment_request_id=payment_request_id,
            ln_node_id=self._store.ln_node_id,
            value_msat=payment.value_msat,
            status=PaymentStatus(payment.status),
            creation_time=_ns_to_datetime(payment.creation_time_ns),
            fee_msat=payment.fee_msat,
            payment_index=payment.payment_index,
            failure_reason=PaymentFailureReason(payment.failure_reason),
        )

    def _convert_htlc_attempt(
        self, attempt: ln.HTLCAttempt, payment: Payment
    ) -> HTLCAttempt:

        if attempt.resolve_time_ns > 0:
            resolve_time = _ns_to_datetime(attempt.resolve_time_ns)
        else:
            resolve_time = None

        # Determination of the index of the last used hop. It is the failure source
        # index if the attempt failed. If the attempt succeeded it is the receiver
        # of the attempt.
        if attempt.status == 2 and attempt.failure is not None:
            last_used_hop_index = attempt.failure.failure_source_index
        elif attempt.status == 1:
            last_used_hop_index = len(attempt.route.hops)
        else:
            last_used_hop_index = None

        route = self._convert_route(attempt.route, last_used_hop_index)

        htlc_attempt = HTLCAttempt(
            payment=payment,
            attempt_id=attempt.attempt_id,
            status=HTLCStatus(attempt.status),
            attempt_time=_ns_to_datetime(attempt.attempt_time_ns),
            resolve_time=resolve_time,
            route=route,
        )

        # If the attempt failed we store the failure information. For succeeded
        # attempts we don't need to store this information.
        if attempt.status == 2 and attempt.failure is not None:
            try:
                source_hop = route.hops[attempt.failure.failure_source_index]
            except IndexError:
                source_hop = None
                logging.warning(
                    f"Failure source index out of bounds: {attempt.failure.failure_source_index=}, ",
                    f"{attempt.attempt_id=}",
                )
            htlc_attempt.failure = _convert_failure(
                attempt.failure, htlc_attempt, source_hop
            )

        return htlc_attempt

    def _convert_route(self, route: ln.Route, last_used_hop_index: int | None) -> Route:

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

        if last_used_hop_index is not None and last_used_hop_index >= 0:
            path_success = path[:last_used_hop_index]
            path_success_id = self._get_graph_path_id(path_success)
        else:
            path_success_id = None

        return Route(
            total_time_lock=route.total_time_lock,
            total_amt_msat=route.total_amt_msat,
            total_fees_msat=route.total_fees_msat,
            hops=hops,
            path_id=path_id,
            path_success_id=path_success_id,
            num_hops_successful=last_used_hop_index,
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
