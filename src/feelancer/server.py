from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from feelancer.data.db import FeelancerDB
from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.client import LndGrpc

if TYPE_CHECKING:
    from feelancer.lightning.client import LightningClient


@dataclass
class ServerConfig:
    db: FeelancerDB
    lnclient: LightningClient

    @classmethod
    def from_config_dict(cls, config_dict: dict) -> ServerConfig:
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

        return cls(db, lnclient)


class Server:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
