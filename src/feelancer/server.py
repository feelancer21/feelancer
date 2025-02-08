from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import FeelancerConfig
from .data.db import FeelancerDB
from .lightning.lnd import LNDClient
from .lnd.client import LndGrpc
from .pid.data import PidConfig
from .pid.service import PidService
from .reconnect.reconnector import LNDReconnector
from .reconnect.service import ReconnectConfig, ReconnectService
from .tasks.runner import TaskRunner
from .utils import read_config_file

if TYPE_CHECKING:
    from feelancer.lightning.client import LightningClient
    from feelancer.reconnect.reconnector import Reconnector


@dataclass
class AppConfig:
    db: FeelancerDB
    lnclient: LightningClient
    config_file: str
    log_file: str | None
    log_level: str | None
    feelancer_cfg: FeelancerConfig
    reconnector: Reconnector

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
        )

    @classmethod
    def from_config_file(cls, file_name: str) -> AppConfig:
        return cls.from_config_dict(read_config_file(file_name), file_name)


class Server:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

        self.lock = threading.Lock()

        # We init a task runner which controls the scheduler for the job
        # execution.
        self.runner = TaskRunner(
            self.cfg.lnclient,
            self.cfg.db,
            self.cfg.feelancer_cfg.seconds,
            self.cfg.feelancer_cfg.max_listener_attempts,
            self.read_feelancer_cfg,
        )

        # pid service is responsible for updating the fees with the pid model.
        pid = PidService(self.cfg.db, self.get_pid_config)
        self.runner.register_task(pid.run)
        self.runner.register_reset(pid.reset)

        # reconnect service is responsible for reconnecting inactive channels
        # or channels with stuck htlcs.
        reconnect = ReconnectService(cfg.reconnector, self.get_reconnect_config)
        self.runner.register_task(reconnect.run)

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

    def start(self) -> None:
        """
        Starts the server.
        """

        self.runner.start()

    def stop(self) -> None:
        """
        Stops the server.
        """

        with self.lock:
            self.runner.stop()
