import datetime
import hashlib
from collections.abc import Generator, Iterable
from typing import Protocol

import pytz

from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.client import LndGrpc
from feelancer.lnd.grpc_generated import lightning_pb2 as ln

from .models import Failure, FailureCode, Hop, HTLCAttempt, HTLCStatus


# Helper function for converting nanoseconds to a datetime object.
def _ns_to_datetime(ns: int) -> datetime.datetime:
    """Convert UNIX nanoseconds to a timezone-aware datetime (UTC)."""
    return datetime.datetime.fromtimestamp(ns / 1e9, tz=pytz.utc)


def _sha256_of_hops(hops: Iterable[ln.Hop]) -> str:
    """Creates the sha256sum of the concatenation of all public keys."""

    # concat all the pub keys
    pubkeys = "".join([h.pub_key for h in hops])
    return hashlib.sha256(pubkeys.encode("utf-8")).hexdigest()


def _convert_failure(failure: ln.Failure, source_hop: Hop) -> Failure:
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


def _convert_htlc_attempt(attempt: ln.HTLCAttempt, node_id: int) -> HTLCAttempt:

    if attempt.resolve_time_ns > 0:
        resolve_time = _ns_to_datetime(attempt.resolve_time_ns)
    else:
        resolve_time = None

    hops: list[Hop] = [_convert_hop(hop, i) for i, hop in enumerate(attempt.route.hops)]

    if attempt.failure is not None:
        source_hop = hops[attempt.failure.failure_source_index]
        failure = _convert_failure(attempt.failure, source_hop)
    else:
        failure = None

    return HTLCAttempt(
        ln_node_id=node_id,
        attempt_id=attempt.attempt_id,
        status=HTLCStatus(attempt.status),
        attempt_time=_ns_to_datetime(attempt.attempt_time_ns),
        resolve_time=resolve_time,
        hops=hops,
        failure=failure,
        hops_sha256_sum=_sha256_of_hops(attempt.route.hops),
    )


def _yield_attempts_from_payment(
    payment: ln.Payment,
) -> Generator[ln.HTLCAttempt]:

    # we only process status SUCCEEDED or FAILED
    if payment.status not in [2, 3]:
        return None

    yield from payment.htlcs


class PaymentTracker(Protocol):

    def generate_attempts(self, ln_node_id: int) -> Generator[HTLCAttempt]: ...

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

    def generate_attempts(self, ln_node_id: int) -> Generator[HTLCAttempt]:

        def convert(p: ln.Payment) -> Generator[HTLCAttempt]:
            for h in _yield_attempts_from_payment(p):
                yield _convert_htlc_attempt(h, ln_node_id)

        subscription: Generator[Generator[HTLCAttempt]]
        subscription = self.lnd.lnd.track_payments_dispatcher.subscribe(convert)

        for s in subscription:
            yield from s
