from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

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


class SubServer(Protocol):
    """
    A subserver is a service running as a daemon and have to be started and stopped.
    """

    def start(self) -> None: ...
    def stop(self) -> None: ...


@dataclass
class AppConfig:
    db: FeelancerDB
    lnclient: LightningClient
    config_file: str
    log_file: str | None
    log_level: str | None
    feelancer_cfg: FeelancerConfig
    reconnector: Reconnector
    payment_tracker: PaymentTracker

    @classmethod
    def from_config_dict(cls, config_dict: dict, config_file: str) -> AppConfig:
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
            payment_tracker: PaymentTracker = LNDPaymentTracker(lndgrpc)
        else:
            raise ValueError("'lnd' section is not included in config-file")

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
        )

    @classmethod
    def from_config_file(cls, file_name: str) -> AppConfig:
        return cls.from_config_dict(read_config_file(file_name), file_name)


class Server:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

        # Threads to be started during start
        self._threads_start: list[threading.Thread] = []

        # Threads to be started during stop
        self._threads_stop: list[threading.Thread] = []

        # Adding callables for starting and stopping internal services of the
        # lnclient, e.g. dispatcher of streams.
        for starter in self.cfg.lnclient.get_starter():
            self._register_starter(starter)

        for stopper in self.cfg.lnclient.get_stopper():
            self._register_stopper(stopper)

        # We init a task runner which controls the scheduler for the job
        # execution.
        runner = TaskRunner(
            self.cfg.lnclient,
            self.cfg.db,
            self.cfg.feelancer_cfg.seconds,
            self.cfg.feelancer_cfg.max_listener_attempts,
            self.read_feelancer_cfg,
        )

        # pid service is responsible for updating the fees with the pid model.
        pid = PidService(
            self.cfg.db, self.cfg.lnclient.pubkey_local, self.get_pid_config
        )
        runner.register_task(pid.run)
        runner.register_reset(pid.reset)
        self._register_sub_server(runner)

        # reconnect service is responsible for reconnecting inactive channels
        # or channels with stuck htlcs.
        reconnect = ReconnectService(cfg.reconnector, self.get_reconnect_config)
        runner.register_task(reconnect.run)

        paytrack_conf = self.get_paytrack_config()
        paytrack_service: PaytrackService | None = None
        if paytrack_conf is not None:
            paytrack_service = PaytrackService(
                db=self.cfg.db,
                payment_tracker=self.cfg.payment_tracker,
                paytrack_config=paytrack_conf,
            )
            self._register_sub_server(paytrack_service)

        # Lock will be released after start of the subservers has finished.
        self.lock = threading.Lock()
        self.lock.acquire()

    def _register_sub_server(self, subserver: SubServer) -> None:
        self._register_starter(subserver.start)
        self._register_stopper(subserver.stop)

    def _register_starter(self, start: Callable[...]) -> None:
        self._threads_start.append(threading.Thread(target=start))

    def _register_stopper(self, stop: Callable[...]) -> None:
        self._threads_stop.append(threading.Thread(target=stop))

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

    def get_pid_config(self) -> PidConfig:
        return PidConfig(self.cfg.feelancer_cfg.tasks_config["pid"])

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

    def start(self) -> None:
        """
        Starts the server.
        """

        for t in self._threads_start:
            t.start()

        self.lock.release()

        logging.debug("All start threads started")

        for t in self._threads_start:
            t.join()

        logging.debug("All start threads joined")

    def stop(self) -> None:
        """
        Stops the server.
        """

        with self.lock:
            for t in self._threads_stop:
                t.start()

        logging.debug("All stop threads started")

        for t in self._threads_stop:
            t.join()

        logging.debug("All stop threads joined")
