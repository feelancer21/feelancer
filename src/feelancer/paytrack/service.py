import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import Select

from feelancer.base import BaseServer, default_retry_handler
from feelancer.tasks.runner import RunnerRequest, RunnerResult

from .data import query_average_node_speed, query_slow_nodes
from .tracker import PaymentTracker

DEFAULT_NODE_SPEED_WRITE_CSV = False
DEFAULT_NODE_SPEED_CSV_FILE = "~/.feelancer/node_speed.csv"
DEFAULT_NODE_SPEED_TIME_WINDOW_HOURS = 48
DEFAULT_NODE_SPEED_HTLC_TIME_CAP = 60.0

DEFAULT_SLOW_NODES_WRITE_CSV = False
DEFAULT_SLOW_NODES_CSV_FILE = "~/.feelancer/slow_nodes.csv"
DEFAULT_SLOW_NODES_MIN_ATTEMPTS = 0
DEFAULT_SLOW_NODES_MIN_SPEED = 21.0


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
            self.node_speed_htlc_time_cap = float(
                config_dict.get(
                    "node_speed_htlc_time_cap", DEFAULT_NODE_SPEED_HTLC_TIME_CAP
                )
            )
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
            self.slow_nodes_min_speed = float(
                config_dict.get("slow_nodes_min_speed", DEFAULT_SLOW_NODES_MIN_SPEED)
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
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._payment_tracker = payment_tracker
        self._get_paytrack_config = get_paytrack_config
        self._to_csv = to_csv

        self._register_starter(self._start_server)
        self._register_stopper(self._stop_server)

    def run(self, request: RunnerRequest) -> RunnerResult:
        """
        Creates the csv files with node speed and slow nodes.
        """

        config = self._get_paytrack_config()
        if config is None:
            return RunnerResult()

        logging.info(f"{self._name} running...")
        logging.debug(f"{self._name} {config.__dict__=}")

        if config.node_speed_write_csv:
            qry = query_average_node_speed(
                start_time=request.timestamp
                + timedelta(hours=-config.node_speed_time_window_hours),
                end_time=request.timestamp,
                htlc_time_cap=config.node_speed_htlc_time_cap,
            )
            header = ["pub_key", "average_speed_sec", "num_attempts"]
            self._to_csv(qry, config.node_speed_csv_file, header)

        if config.slow_nodes_write_csv:
            qry = query_slow_nodes(
                start_time=request.timestamp
                + timedelta(hours=-config.node_speed_time_window_hours),
                end_time=request.timestamp,
                htlc_time_cap=config.node_speed_htlc_time_cap,
                min_average_speed=config.slow_nodes_min_speed,
                min_num_attempts=config.slow_nodes_min_attempts,
            )
            self._to_csv(qry, config.slow_nodes_csv_file, None)
        
        logging.info(f"{self._name} finished...")
        
        return RunnerResult()

    @default_retry_handler
    def _start_server(self) -> None:
        """Start of storing of payments in the store."""

        self._payment_tracker.store_payments()

    def _stop_server(self) -> None:
        # Service ends when the incoming payment stream has exhausted.
        # But we have to stop the pre sync with is started synchronously.

        self._payment_tracker.pre_sync_stop()
        return None
