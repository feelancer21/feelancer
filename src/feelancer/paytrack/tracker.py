from collections.abc import Generator
from typing import Protocol

from feelancer.grpc.client import GrpcStreamClient
from feelancer.lnd.client import LndGrpc
from feelancer.lnd.grpc_generated import lightning_pb2 as ln


class PaymentTracker(Protocol):

    def generate_attempts(self) -> Generator[ln.HTLCAttempt]: ...

    def start(self) -> None:
        """
        Starts the payment tracker.
        """
        ...

    def stop(self) -> None:
        """
        Stops the payment tracker.
        """
        ...


class LNDPaymentTracker(GrpcStreamClient[ln.Payment]):

    def __init__(self, lnd: LndGrpc):

        super().__init__(name="LndPaymentTracker", producer=lnd.track_payments)

    def generate_attempts(self) -> Generator[ln.HTLCAttempt]:

        for p in self.generate_messages():
            # we only process status SUCCEEDED or FAILED
            if p.status not in [2, 3]:
                continue

            for h in p.htlcs:

                yield h
