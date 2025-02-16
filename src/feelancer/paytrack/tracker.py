import logging
from collections.abc import Generator
from typing import Protocol

from feelancer.lightning.lnd import LNDClient


class PaymentTracker(Protocol):

    def generate_attempts(self) -> Generator: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...


class LNDPaymentTracker(LNDClient):

    def generate_attempts(self) -> Generator: ...

    def start(self) -> None:
        logging.debug("Paytrack start")

    def stop(self) -> None:
        logging.debug("Paytrack stop")
