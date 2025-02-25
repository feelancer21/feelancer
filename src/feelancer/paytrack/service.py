from feelancer.base import BaseServer, default_retry_handler

from .tracker import PaymentTracker


# A config. But it is only a dummy at the moment.
class PaytrackConfig:
    def __init__(self, config_dict: dict) -> None: ...


class PaytrackService(BaseServer):
    """
    Receiving of payment data from a stream and storing in the database.
    """

    def __init__(
        self,
        payment_tracker: PaymentTracker,
        paytrack_config: PaytrackConfig,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._payment_tracker = payment_tracker
        self._paytrack_config = paytrack_config

        self._register_starter(self._start_server)
        self._register_stopper(self._stop_server)

    @default_retry_handler
    def _start_server(self) -> None:
        """Start of storing of payments in the store."""

        self._payment_tracker.store_payments()

    def _stop_server(self) -> None:
        # Service ends when the incoming payment stream has exhausted.
        # But we have to stop the pre sync with is started synchronously.

        self._payment_tracker.pre_sync_stop()
        return None
