from collections.abc import Callable, Iterable
from datetime import timedelta

from sqlalchemy import Delete, Select

from feelancer.log import getLogger
from feelancer.tasks.runner import RunnerRequest, RunnerResult

from .data import (
    delete_failed_htlcs,
    delete_failed_transactions,
    query_average_node_speed,
    query_liquidity_locked_per_htlc,
    query_slow_nodes,
)

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
logger = getLogger(__name__)


def _validate_percentiles(percentiles: list[int]) -> None:
    for p in percentiles:
        if p > 100 or p < 0:
            raise ValueError(f"Invalid percentile {p=}. Must be between 0 and 100.")


class TrackerConfig:
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


class TrackerService:
    """
    Receiving of payment data from a stream and storing in the database.
    """

    def __init__(
        self,
        get_config: Callable[[], TrackerConfig | None],
        db_to_csv: Callable[[Select[tuple], str, list[str] | None], None],
        db_delete_data: Callable[[Iterable[Delete[tuple]]], None],
    ) -> None:

        self._get_config = get_config
        self._db_to_csv = db_to_csv
        self._db_delete_data = db_delete_data

    def run(self, request: RunnerRequest) -> RunnerResult:
        """
        Creates the csv files with node speed and slow nodes.
        """

        config = self._get_config()
        if config is None:
            return RunnerResult()

        logger.info("Start run...")
        logger.debug(f"Config used for run {config.__dict__=}")

        if config.node_speed_write_csv:
            qry, header = query_average_node_speed(
                start_time=request.timestamp
                + timedelta(hours=-config.node_speed_time_window_hours),
                end_time=request.timestamp,
                percentiles=config.node_speed_percentiles,
            )
            self._db_to_csv(qry, config.node_speed_csv_file, header)

        if config.slow_nodes_write_csv:
            qry = query_slow_nodes(
                start_time=request.timestamp
                + timedelta(hours=-config.node_speed_time_window_hours),
                end_time=request.timestamp,
                percentile=config.slow_nodes_percentile,
                min_speed=config.slow_nodes_min_speed,
                min_num_attempts=config.slow_nodes_min_attempts,
            )
            self._db_to_csv(qry, config.slow_nodes_csv_file, None)

        if config.htlc_liquidity_locked_write_csv:
            qry, header = query_liquidity_locked_per_htlc(
                start_time=request.timestamp
                + timedelta(hours=-config.node_speed_time_window_hours),
                end_time=request.timestamp,
            )

            self._db_to_csv(qry, config.htlc_liquidity_locked_csv_file, header)

        # Housekeeping to delete failed htlc attempts
        if config.delete_failed:

            deletion_cutoff = request.timestamp
            deletion_cutoff += timedelta(hours=-config.delete_failed_hours)

            queries = []
            # First we delete the failed transactions, then the failed htlcs.
            queries.append(delete_failed_transactions(deletion_cutoff))
            queries.append(delete_failed_htlcs(deletion_cutoff))

            self._db_delete_data(queries)

        logger.info("Finished run...")

        return RunnerResult()
