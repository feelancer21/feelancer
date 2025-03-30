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
from typing import TYPE_CHECKING, TypeVar

from .base import BaseServer
from .config import DictInitializedConfig, FeelancerConfig
from .data.db import FeelancerDB
from .event import stop_event
from .lightning.data import LightningStore
from .lightning.lnd import LNDClient
from .lnd.client import LndGrpc
from .pid.data import PidConfig, PidStore
from .pid.service import PidService
from .reconnect.reconnector import LNDReconnector
from .reconnect.service import ReconnectConfig, ReconnectService
from .tasks.runner import TaskRunner
from .tracker.data import TrackerStore
from .tracker.forwards.lnd import LNDForwardTracker
from .tracker.forwards.service import FwdtrackConfig, FwdtrackService
from .tracker.invoices.lnd import LNDInvoiceTracker
from .tracker.invoices.service import InvtrackConfig, InvtrackService
from .tracker.payments.lnd import LNDPaymentTracker
from .tracker.payments.service import PaytrackConfig, PaytrackService
from .utils import read_config_file

T = TypeVar("T", bound=DictInitializedConfig)

if TYPE_CHECKING:
    from feelancer.lightning.client import LightningClient
    from feelancer.reconnect.reconnector import Reconnector
    from feelancer.tracker.proto import Tracker, TrackerService


DEFAULT_TIMEOUT = 180
TRACEBACK_DUMP_FILE = "traceback_dump.txt"
FAULTHANDLER_DUMP_FILE = "faulthandler_dump.txt"
logger = logging.getLogger(__name__)


@dataclass
class MainConfig:
    db: FeelancerDB
    lnclient: LightningClient
    ln_store: LightningStore
    config_file: str
    log_file: str | None
    log_level: str | None
    feelancer_cfg: FeelancerConfig
    reconnector: Reconnector
    payment_tracker: Tracker
    invoice_tracker: Tracker
    forward_tracker: Tracker
    tracker_store: TrackerStore
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

            pub_key = lnclient.pubkey_local
            ln_store = LightningStore(db, pub_key)
            tracker_store = TrackerStore(db, ln_store.ln_node_id)
            payment_tracker: Tracker = LNDPaymentTracker(lnclient, tracker_store)
            invoice_tracker: Tracker = LNDInvoiceTracker(lnclient, tracker_store)
            forward_tracker: Tracker = LNDForwardTracker(lnclient, tracker_store)
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
            ln_store,
            config_file,
            logfile,
            loglevel,
            feelancer_config,
            reconnector,
            payment_tracker,
            invoice_tracker,
            forward_tracker,
            tracker_store,
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

        logger.debug(f"Received {signal.Signals(signum).name}")

        # Activate the timeout signal if it is set.
        if self._timeout is not None:
            logger.debug(f"Setting {self._timeout=}")
            signal.alarm(self._timeout)
            signal.signal(signal.SIGALRM, self._receive_alarm)

        self._sig_handler()
        logger.debug("Signal handler called.")

    def _receive_alarm(self, signum, frame) -> None:
        """Action if SIGALARM is received."""

        logger.debug(f"Received {signal.Signals(signum).name}")

        self._alarm_handler()

    def _receive_sigusr1(self, signum, frame):
        """Dump all thread stack traces using faulthandler to a file."""

        logger.debug(f"Received {signal.Signals(signum).name}")

        dump_file = (
            f"{datetime.now().strftime("%Y%m%d_%H%M%S")}_{FAULTHANDLER_DUMP_FILE}"
        )
        with open(dump_file, "w") as f:
            # Dump stack traces of all threads into the file
            faulthandler.dump_traceback(file=f, all_threads=True)
        logger.debug(f"Faulthandler dump written to {dump_file=}.")

    def _receive_sigusr2(self, signum, frame):
        """Dump all thread stack traces using the traceback module to another file."""

        logger.debug(f"Received {signal.Signals(signum).name}")

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

        logger.debug(f"Traceback dump written to {dump_file=}.")


class MainServer(BaseServer):
    def __init__(self, cfg: MainConfig) -> None:
        super().__init__()
        self.cfg = cfg

        # Stopping all retry handlers if the server is stopped.
        self._register_sync_stopper(stop_event.set)

        # Setting up the signal handler for SIGTERM and SIGINT.
        SignalHandler(self.stop, self.kill, self.cfg.timeout)

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
        pid_store = PidStore(self.cfg.db, self.cfg.lnclient.pubkey_local)
        pid = PidService(
            self.cfg.ln_store,
            pid_store,
            self._get_config_reader("pid", PidConfig),
        )
        runner.register_task(pid.run)
        runner.register_reset(pid.reset)

        # reconnect service is responsible for reconnecting inactive channels
        # or channels with stuck htlcs.
        reconnect = ReconnectService(
            cfg.reconnector, self._get_config_reader("reconnect", ReconnectConfig)
        )
        runner.register_task(reconnect.run)

        self._register_tracker_service(
            cfg.payment_tracker, runner, "paytrack", PaytrackConfig, PaytrackService
        )

        self._register_tracker_service(
            cfg.invoice_tracker, runner, "invtrack", InvtrackConfig, InvtrackService
        )

        self._register_tracker_service(
            cfg.forward_tracker, runner, "fwdtrack", FwdtrackConfig, FwdtrackService
        )

    def _register_tracker_service(
        self,
        tracker: Tracker,
        runner: TaskRunner,
        service_name: str,
        conf_type: type[T],
        service_type: type[TrackerService[T]],
    ) -> None:

        get_config = self._get_config_reader(service_name, conf_type)

        # We only register the service and the tracker if we have a config for it.
        conf = get_config()
        if conf is not None:
            service = service_type(
                get_config=get_config,
                db_to_csv=self.cfg.db.query_all_to_csv,
                db_delete_data=self.cfg.db.core_delete,
            )
            runner.register_task(service.run)
            self._register_tracker(tracker)

    def _register_tracker(self, tracker: Tracker) -> None:
        self._register_sync_starter(tracker.pre_sync_start)
        self._register_starter(tracker.start)

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
            logger.error("An error occurred during the update of the config: %s", e)
            # Using the current config as fallback

        return self.cfg.feelancer_cfg

    def _get_config_reader(self, name: str, type: type[T]) -> Callable[..., T | None]:
        def get_config() -> T | None:
            config_dict = self.cfg.feelancer_cfg.tasks_config.get(name)
            if config_dict is None:
                return None

            return type(config_dict)

        return get_config

    def kill(self) -> None:
        """
        Kills the server.
        """

        self._logger.info("{Killing...\n")
        os._exit(1)
