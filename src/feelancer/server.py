from __future__ import annotations

import faulthandler
import logging
import os
import pprint
import signal
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .base import BaseServer
from .config import FeelancerConfig
from .data.db import FeelancerDB
from .lightning.lnd import LNDClient
from .lnd.client import LndGrpc
from .paytrack.service import PaytrackConfig, PaytrackService
from .paytrack.tracker import LNDPaymentTracker
from .pid.data import PidConfig
from .pid.service import PidService
from .reconnect.reconnector import LNDReconnector
from .reconnect.service import ReconnectConfig, ReconnectService
from .tasks.runner import TaskRunner
from .utils import read_config_file

if TYPE_CHECKING:
    from feelancer.lightning.client import LightningClient
    from feelancer.paytrack.tracker import PaymentTracker
    from feelancer.reconnect.reconnector import Reconnector

DEFAULT_TIMEOUT = 180
TRACEBACK_DUMP_FILE = "traceback_dump.txt"
FAULTHANDLER_DUMP_FILE = "faulthandler_dump.txt"


@dataclass
class MainConfig:
    db: FeelancerDB
    lnclient: LightningClient
    config_file: str
    log_file: str | None
    log_level: str | None
    feelancer_cfg: FeelancerConfig
    reconnector: Reconnector
    payment_tracker: PaymentTracker
    timeout: int

    @classmethod
    def from_config_dict(cls, config_dict: dict, config_file: str) -> MainConfig:
        """
        Initializes the ServerConfig with the config dictionary.
        """

        if "sqlalchemy" in config_dict:
            db = FeelancerDB.from_config_dict(config_dict["sqlalchemy"]["url"])
        else:
            raise ValueError("'sqlalchemy' section is not included in config-file")

        if "lnd" in config_dict:
            lndgrpc = LndGrpc.from_file(**config_dict["lnd"])
            lnclient: LightningClient = LNDClient(lndgrpc)
            reconnector: Reconnector = LNDReconnector(lndgrpc)
            payment_tracker: PaymentTracker = LNDPaymentTracker(lndgrpc, db)
        else:
            raise ValueError("'lnd' section is not included in config-file")

        if (timeout := config_dict.get("timeout")) is not None:
            timeout = int(timeout)
        else:
            timeout = DEFAULT_TIMEOUT

        logfile = None
        loglevel = None
        if (logging := config_dict.get("logging")) is not None:
            logfile = logging.get("logfile")
            loglevel = logging.get("level")

            if logfile is not None:
                logfile = str(logfile)

            if loglevel is not None:
                loglevel = str(loglevel)

        # TODO: Move FeelancerConfig to db and api. Then we can remove it.
        feelancer_config = FeelancerConfig(config_dict)

        return cls(
            db,
            lnclient,
            config_file,
            logfile,
            loglevel,
            feelancer_config,
            reconnector,
            payment_tracker,
            timeout,
        )

    @classmethod
    def from_config_file(cls, file_name: str) -> MainConfig:
        return cls.from_config_dict(read_config_file(file_name), file_name)


class SignalHandler:
    """
    Signal handler for SIGTERM and SIGINT signals.
    """

    def __init__(
        self,
        sig_handler: Callable[..., None],
        alarm_handler: Callable[..., None],
        timeout: int,
    ) -> None:
        self._timeout = timeout
        self._sig_handler = sig_handler
        self._alarm_handler = alarm_handler

        self._lock = threading.Lock()
        self._sig_received = False

        # If one signal is received, self._receive_signal is called
        signal.signal(signal.SIGTERM, self._receive_sig)
        signal.signal(signal.SIGINT, self._receive_sig)
        signal.signal(signal.SIGUSR1, self._receive_sigusr1)
        signal.signal(signal.SIGUSR2, self._receive_sigusr2)

    def _receive_sig(self, signum, frame) -> None:
        """Action if SIGTERM or SIGINT is received."""

        with self._lock:
            if self._sig_received:
                return
            self._sig_received = True

        logging.debug(f"Received {signal.Signals(signum).name}")

        # Activate the timeout signal if it is set.
        if self._timeout is not None:
            logging.debug(f"Setting {self._timeout=}")
            signal.alarm(self._timeout)
            signal.signal(signal.SIGALRM, self._receive_alarm)

        self._sig_handler()
        logging.debug("Signal handler called.")

    def _receive_alarm(self, signum, frame) -> None:
        """Action if SIGALARM is received."""

        logging.debug(f"Received {signal.Signals(signum).name}")

        self._alarm_handler()

    def _receive_sigusr1(self, signum, frame):
        """Dump all thread stack traces using faulthandler to a file."""

        logging.debug(f"Received {signal.Signals(signum).name}")

        dump_file = (
            f"{datetime.now().strftime("%Y%m%d_%H%M%S")}_{FAULTHANDLER_DUMP_FILE}"
        )
        with open(dump_file, "w") as f:
            # Dump stack traces of all threads into the file
            faulthandler.dump_traceback(file=f, all_threads=True)
        logging.debug(f"Faulthandler dump written to {dump_file=}.")

    def _receive_sigusr2(self, signum, frame):
        """Dump all thread stack traces using the traceback module to another file."""

        logging.debug(f"Received {signal.Signals(signum).name}")

        dump_file = f"{datetime.now().strftime("%Y%m%d_%H%M%S")}_{TRACEBACK_DUMP_FILE}"
        with open(dump_file, "w") as f:

            f.write("Dump of all thread stack traces:\n")
            for thread_id, stack in sys._current_frames().items():
                f.write(f"\nThread ID: {thread_id}\n")
                traceback.print_stack(stack, file=f)

                f.write("Local variables per frame:\n")
                current_frame = stack
                while current_frame:
                    f.write(
                        f"\nFrame '{current_frame.f_code.co_name}' "
                        f"(Line {current_frame.f_lineno}):\n"
                    )
                    # Pretty-print the local variables
                    f.write(pprint.pformat(current_frame.f_locals, indent=4))
                    f.write("\n")
                    current_frame = current_frame.f_back

        logging.debug(f"Traceback dump written to {dump_file=}.")


class MainServer(BaseServer):
    def __init__(self, cfg: MainConfig) -> None:
        super().__init__()
        self.cfg = cfg

        # Setting up the signal handler for SIGTERM and SIGINT.
        SignalHandler(self.stop, self.kill, self.cfg.timeout)

        # Adding callables for starting and stopping internal services of the
        # lnclient, e.g. dispatcher of streams.
        self._register_sub_server(cfg.lnclient)

        # We init a task runner which controls the scheduler for the job
        # execution.
        runner = TaskRunner(
            self.cfg.lnclient,
            self.cfg.db,
            self.cfg.feelancer_cfg.seconds,
            self.cfg.feelancer_cfg.max_listener_attempts,
            self.read_feelancer_cfg,
        )
        self._register_sub_server(runner)

        # pid service is responsible for updating the fees with the pid model.
        pid = PidService(
            self.cfg.db, self.cfg.lnclient.pubkey_local, self.get_pid_config
        )
        runner.register_task(pid.run)
        runner.register_reset(pid.reset)

        # reconnect service is responsible for reconnecting inactive channels
        # or channels with stuck htlcs.
        reconnect = ReconnectService(cfg.reconnector, self.get_reconnect_config)
        runner.register_task(reconnect.run)

        paytrack_conf = self.get_paytrack_config()
        paytrack_service: PaytrackService | None = None
        if paytrack_conf is not None:
            paytrack_service = PaytrackService(
                payment_tracker=self.cfg.payment_tracker,
                get_paytrack_config=self.get_paytrack_config,
                to_csv=self.cfg.db.query_all_to_csv,
                delete_data=self.cfg.db.core_delete,
            )
            self._register_sub_server(paytrack_service)

            # Pre sync before threadpool execution starts to sync faster
            self._register_sync_starter(
                paytrack_service._payment_tracker.pre_sync_start
            )

            runner.register_task(paytrack_service.run)

    def read_feelancer_cfg(self) -> FeelancerConfig:
        """
        Reads the config file and init a new Feelancer Config.
        """

        # TODO: Remove when we have interval in api

        try:
            self.cfg.feelancer_cfg = FeelancerConfig(
                read_config_file(self.cfg.config_file)
            )
        except Exception as e:
            logging.error("An error occurred during the update of the config: %s", e)
            # Using the current config as fallback

        return self.cfg.feelancer_cfg

    def get_pid_config(self) -> PidConfig | None:
        config_dict = self.cfg.feelancer_cfg.tasks_config.get("pid")
        if config_dict is None:
            return None

        return PidConfig(config_dict)

    def get_reconnect_config(self) -> ReconnectConfig | None:
        config_dict = self.cfg.feelancer_cfg.tasks_config.get("reconnect")
        if config_dict is None:
            return None

        return ReconnectConfig(config_dict)

    def get_paytrack_config(self) -> PaytrackConfig | None:
        config_dict = self.cfg.feelancer_cfg.tasks_config.get("paytrack")
        if config_dict is None:
            return None

        return PaytrackConfig(config_dict)

    def kill(self) -> None:
        """
        Kills the server.
        """

        logging.info(f"{self._name} killing...\n")
        os._exit(1)
