import datetime
import hashlib
import logging
from collections.abc import Callable, Generator, Iterable
from typing import Protocol

import pytz
from google.protobuf.json_format import MessageToDict

from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.client import LndGrpc
from feelancer.lnd.grpc_generated import lightning_pb2 as ln

from .data import PaymentNotFound
from .models import Failure, FailureCode, Hop, HTLCAttempt, HTLCStatus, Payment, Route


# Helper function for converting nanoseconds to a datetime object.
def _ns_to_datetime(ns: int) -> datetime.datetime:
    """Convert UNIX nanoseconds to a timezone-aware datetime (UTC)."""
    return datetime.datetime.fromtimestamp(ns / 1e9, tz=pytz.utc)


def _sha256_of_hops(hops: Iterable[ln.Hop]) -> str:
    """Creates the sha256sum of the concatenation of all public keys."""

    # concat all the pub keys
    pubkeys = "".join([h.pub_key for h in hops])
    return hashlib.sha256(pubkeys.encode("utf-8")).hexdigest()


def _sha256_payment(payment: ln.Payment | ln.HTLCAttempt) -> str:
    """Creates the sha256sum of the payment object."""
    return hashlib.sha256(payment.SerializeToString(deterministic=True)).hexdigest()


def _convert_failure(failure: ln.Failure, source_hop: Hop | None) -> Failure:
    return Failure(
        code=FailureCode(failure.code),
        source_index=failure.failure_source_index,
        source_hop=source_hop,
    )


def _convert_hop(hop: ln.Hop, pos_id) -> Hop:
    return Hop(
        position_id=pos_id,
        pub_key=hop.pub_key,
        expiry=hop.expiry,
        amt_to_forward_msat=hop.amt_to_forward_msat,
        fee_msat=hop.fee_msat,
    )


def _convert_route(route: ln.Route) -> Route:
    return Route(
        total_time_lock=route.total_time_lock,
        total_amt_msat=route.total_amt_msat,
        total_fees_msat=route.total_fees_msat,
        first_hop_amount_msat=route.first_hop_amount_msat,
        hops=[_convert_hop(hop, i) for i, hop in enumerate(route.hops)],
        hops_num=len(route.hops),
        hops_sha256_sum=_sha256_of_hops(route.hops),
    )


def _convert_htlc_attempt(
    attempt: ln.HTLCAttempt, node_id: int, payment_id
) -> HTLCAttempt:

    if attempt.resolve_time_ns > 0:
        resolve_time = _ns_to_datetime(attempt.resolve_time_ns)
    else:
        resolve_time = None

    route = _convert_route(attempt.route)

    if attempt.failure is not None:
        if attempt.failure.failure_source_index > 0:
            try:
                source_hop = route.hops[attempt.failure.failure_source_index - 1]
            except IndexError:
                source_hop = None
                logging.warning(
                    f"Failure source index out of bounds: {attempt.failure.failure_source_index=}, ",
                    f"{attempt.attempt_id=}",
                )

        else:
            source_hop = None
        failure = _convert_failure(attempt.failure, source_hop)
    else:
        failure = None

    return HTLCAttempt(
        ln_node_id=node_id,
        payment_id=payment_id,
        attempt_id=attempt.attempt_id,
        status=HTLCStatus(attempt.status),
        attempt_time=_ns_to_datetime(attempt.attempt_time_ns),
        resolve_time=resolve_time,
        route=route,
        failure=failure,
    )


def _convert_payment(payment: ln.Payment) -> Payment:
    """
    Converts a payment object from the LND gRPC API to a Payment
    """

    return Payment(
        payment_hash=payment.payment_hash,
        payment_preimage=payment.payment_preimage,
        value_msat=payment.value_msat,
        creation_time=_ns_to_datetime(payment.creation_time_ns),
        fee_msat=payment.fee_msat,
    )


class PaymentTracker(Protocol):

    def generate_attempts(
        self,
        ln_node_id: int,
        get_payment_id: Callable[[str], int],
        add_payment: Callable[[Payment], int],
    ) -> Generator[HTLCAttempt]: ...

    @property
    def pubkey_local(self) -> str:
        """
        Returns the pubkey of the local node.
        """
        ...


class LNDPaymentTracker:

    def __init__(self, lnd: LndGrpc):

        self.lnd = LNDClient(lnd)

    @property
    def pubkey_local(self) -> str:
        return self.lnd.pubkey_local

    def generate_attempts(
        self,
        ln_node_id: int,
        get_payment_id: Callable[[str], int],
        add_payment: Callable[[Payment], int],
    ) -> Generator[HTLCAttempt]:

        # Callback function for the subscription. Converts the payment object
        # to an Iterable of HTLCAttempt objects.
        def convert(p: ln.Payment) -> Iterable[HTLCAttempt]:

            # only process status SUCCEEDED or FAILED
            if p.status not in [2, 3]:
                return

            payment_id: int

            # Check if we have already stored the payment in the database.
            # Maybe from the last run.
            try:
                payment_id = get_payment_id(p.payment_hash)
            except PaymentNotFound as e:
                logging.warning(e)
                # If found not we store it and get the id of the payment.
                payment_id = add_payment(_convert_payment(p))

            logging.debug(f"payment {p.payment_hash=}:\n {MessageToDict(p)}")
            for h in p.htlcs:
                logging.debug(
                    f"payment {p.payment_hash=} {p.status=} {h.attempt_id=} {h.status=} {_sha256_payment(p)=} {_sha256_payment(h)=}"
                )
                yield _convert_htlc_attempt(h, ln_node_id, payment_id)

        subscription: Generator[Iterable[HTLCAttempt]]
        subscription = self.lnd.lnd.track_payments_dispatcher.subscribe(convert)

        for s in subscription:
            yield from s
