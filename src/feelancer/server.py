from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from feelancer.data.db import FeelancerDB
from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.client import LndGrpc
from feelancer.utils import read_config_file

if TYPE_CHECKING:
    from feelancer.lightning.client import LightningClient


@dataclass
class AppConfig:
    db: FeelancerDB
    lnclient: LightningClient
    config_file: str
    log_file: str | None
    log_level: str | None

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
            lnclient = LNDClient(LndGrpc.from_file(**config_dict["lnd"]))
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

        return cls(db, lnclient, config_file, logfile, loglevel)

    @classmethod
    def from_config_file(cls, file_name: str) -> AppConfig:
        return cls.from_config_dict(read_config_file(file_name), file_name)


class Server:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
