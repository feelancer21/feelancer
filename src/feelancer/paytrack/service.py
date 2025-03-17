import logging
from collections.abc import Callable, Iterable
from datetime import timedelta

from sqlalchemy import Delete, Select

from feelancer.base import BaseServer, default_retry_handler
from feelancer.tasks.runner import RunnerRequest, RunnerResult

from .data import (
    delete_failed_htlc_attempts,
    delete_failed_payments,
    query_average_node_speed,
    query_liquidity_locked_per_htlc,
    query_slow_nodes,
)
from .tracker import PaymentTracker

DEFAULT_NODE_SPEED_WRITE_CSV = False
DEFAULT_NODE_SPEED_CSV_FILE = "~/.feelancer/node_speed.csv"
DEFAULT_NODE_SPEED_TIME_WINDOW_HOURS = 48
DEFAULT_NODE_SPEED_PERCENTILES = [50]

DEFAULT_SLOW_NODES_WRITE_CSV = False
DEFAULT_SLOW_NODES_CSV_FILE = "~/.feelancer/slow_nodes.csv"
DEFAULT_SLOW_NODES_MIN_ATTEMPTS = 0
DEFAULT_SLOW_NODES_PERCENTILE = 50
DEFAULT_SLOW_NODES_MIN_SPEED = 21.0

DEFAULT_HTLC_LIQUIDITY_LOCKED_WRITE_CSV = False
DEFAULT_HTLC_LIQUIDITY_LOCKED_CSV_FILE = "~/.feelancer/htlc_liquidity_locked.csv"

DEFAULT_DELETE_FAILED = False
DEFAULT_DELETE_FAILED_HOURS = 168
logger = logging.getLogger(__name__)


def _validate_percentiles(percentiles: list[int]) -> None:
    for p in percentiles:
        if p > 100 or p < 0:
            raise ValueError(f"Invalid percentile {p=}. Must be between 0 and 100.")


# A config. But it is only a dummy at the moment.
class PaytrackConfig:
    def __init__(self, config_dict: dict) -> None:
        """
        Validates the provided dictionary and stores values in variables.
        """

        try:
            self.node_speed_write_csv = bool(
                config_dict.get("node_speed_write_csv", DEFAULT_NODE_SPEED_WRITE_CSV)
            )
            self.node_speed_csv_file = str(
                config_dict.get("node_speed_csv_file", DEFAULT_NODE_SPEED_CSV_FILE)
            )
            self.node_speed_time_window_hours = int(
                config_dict.get(
                    "node_speed_time_window_hours", DEFAULT_NODE_SPEED_TIME_WINDOW_HOURS
                )
            )

            self.node_speed_percentiles = config_dict.get(
                "node_speed_percentiles", DEFAULT_NODE_SPEED_PERCENTILES
            )
            if not isinstance(self.node_speed_percentiles, list):
                raise ValueError(
                    f"Percentiles must be a list {self.node_speed_percentiles=}"
                )

            _validate_percentiles(self.node_speed_percentiles)

            self.slow_nodes_write_csv = bool(
                config_dict.get("slow_nodes_write_csv", DEFAULT_SLOW_NODES_WRITE_CSV)
            )
            self.slow_nodes_csv_file = str(
                config_dict.get("slow_nodes_csv_file", DEFAULT_SLOW_NODES_CSV_FILE)
            )
            self.slow_nodes_min_attempts = int(
                config_dict.get(
                    "slow_nodes_min_attempts", DEFAULT_SLOW_NODES_MIN_ATTEMPTS
                )
            )
            self.slow_nodes_percentile = int(
                config_dict.get("slow_nodes_percentile", DEFAULT_SLOW_NODES_PERCENTILE)
            )
            _validate_percentiles([self.slow_nodes_percentile])

            self.slow_nodes_min_speed = float(
                config_dict.get("slow_nodes_min_speed", DEFAULT_SLOW_NODES_MIN_SPEED)
            )
            self.htlc_liquidity_locked_write_csv = bool(
                config_dict.get(
                    "htlc_liquidity_write_csv", DEFAULT_HTLC_LIQUIDITY_LOCKED_WRITE_CSV
                )
            )
            self.htlc_liquidity_locked_csv_file = str(
                config_dict.get(
                    "htlc_liquidity_locked_csv_file",
                    DEFAULT_HTLC_LIQUIDITY_LOCKED_CSV_FILE,
                )
            )
            self.delete_failed = bool(
                config_dict.get("delete_failed", DEFAULT_DELETE_FAILED)
            )
            self.delete_failed_hours = int(
                config_dict.get("delete_failed_hours", DEFAULT_DELETE_FAILED_HOURS)
            )

        except Exception as e:
            raise ValueError(f"Invalid config: {e}")


class PaytrackService(BaseServer):
    """
    Receiving of payment data from a stream and storing in the database.
    """

    def __init__(
        self,
        payment_tracker: PaymentTracker,
        get_paytrack_config: Callable[..., PaytrackConfig | None],
        to_csv: Callable[[Select[tuple], str, list[str] | None], None],
        delete_data: Callable[[Iterable[Delete[tuple]]], None],
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._payment_tracker = payment_tracker
        self._get_paytrack_config = get_paytrack_config
        self._to_csv = to_csv
        self._delete_data = delete_data

        self._register_starter(self._start_server)
        self._register_stopper(self._stop_server)

    def run(self, request: RunnerRequest) -> RunnerResult:
        """
        Creates the csv files with node speed and slow nodes.
        """

        config = self._get_paytrack_config()
        if config is None:
            return RunnerResult()

        logger.info(f"{self._name} running...")
        logger.debug(f"{self._name} {config.__dict__=}")

        if config.node_speed_write_csv:
            qry, header = query_average_node_speed(
                start_time=request.timestamp
                + timedelta(hours=-config.node_speed_time_window_hours),
                end_time=request.timestamp,
                percentiles=config.node_speed_percentiles,
            )
            self._to_csv(qry, config.node_speed_csv_file, header)

        if config.slow_nodes_write_csv:
            qry = query_slow_nodes(
                start_time=request.timestamp
                + timedelta(hours=-config.node_speed_time_window_hours),
                end_time=request.timestamp,
                percentile=config.slow_nodes_percentile,
                min_speed=config.slow_nodes_min_speed,
                min_num_attempts=config.slow_nodes_min_attempts,
            )
            self._to_csv(qry, config.slow_nodes_csv_file, None)

        if config.htlc_liquidity_locked_write_csv:
            qry, header = query_liquidity_locked_per_htlc(
                start_time=request.timestamp
                + timedelta(hours=-config.node_speed_time_window_hours),
                end_time=request.timestamp,
            )

            self._to_csv(qry, config.htlc_liquidity_locked_csv_file, header)

        # Housekeeping to delete failed htlc attempts
        if config.delete_failed:

            deletion_cutoff = request.timestamp
            deletion_cutoff += timedelta(hours=-config.delete_failed_hours)

            queries = []
            # First we delete the failed payments, then the remaining failed
            # htlc attempts connected with success full the payments
            queries.append(delete_failed_payments(deletion_cutoff))
            queries.append(delete_failed_htlc_attempts(deletion_cutoff))

            self._delete_data(queries)

        logger.info(f"{self._name} finished...")

        return RunnerResult()

    @default_retry_handler
    def _start_server(self) -> None:
        """Start storing new payments."""

        self._payment_tracker.store_payments()

    def _stop_server(self) -> None:
        # Service ends when the incoming payment stream has exhausted.
        # But we have to stop the pre sync with is started synchronously.

        self._payment_tracker.pre_sync_stop()
        return None
