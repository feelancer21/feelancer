from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from feelancer.utils import GenericConf, defaults_from_type, get_peers_config

from .ewma_pid import PidControllerParams

DEFAULT_MAX_AGE_NEW_CHANNELS = 144
DEFAULT_DB_ONLY = True


@dataclass
class PidPeerControllerConfig(GenericConf):
    lambda_epsilon: float = 1e-4
    pid_controller: PidControllerParams = field(
        default_factory=lambda: PidControllerParams()
    )
    target: int | None = None
    feerate_new_local: int = 21000
    feerate_new_remote: int = 0


@dataclass
class PidMarginControllerConfig(GenericConf):
    lambda_epsilon: float = 1e-4
    pid_controller: PidControllerParams = field(
        default_factory=lambda: PidControllerParams()
    )


class PidConfig:
    def __init__(
        self,
        config_dict: dict,
    ) -> None:
        conf_copy = deepcopy(config_dict)

        if not (exclude_pubkeys := conf_copy.get("exclude_pubkeys")):
            self.exclude_pubkeys = []
        elif not isinstance(exclude_pubkeys, list):
            raise TypeError("'pid.exclude_pubkeys' not a list")
        else:
            self.exclude_pubkeys = exclude_pubkeys

        if not (exclude_chanids := conf_copy.get("exclude_chanids")):
            self.exclude_chanids = []
        elif not isinstance(exclude_chanids, list):
            raise TypeError("'pid.exclude_chanids' not a list")
        else:
            self.exclude_chanids = exclude_chanids

        if conf_copy.get("margin"):
            if fl_params := conf_copy["margin"].get("pid_controller"):
                conf_copy["margin"]["pid_controller"] = PidControllerParams(**fl_params)
        self.max_age_new_channels = int(
            conf_copy.get("max_age_new_channels") or DEFAULT_MAX_AGE_NEW_CHANNELS
        )

        if isinstance(db_only := conf_copy.get("db_only"), bool):
            self.db_only = db_only
        elif db_only is None:
            self.db_only = DEFAULT_DB_ONLY
        else:
            raise TypeError("'db_only' is not a bool")

        self.margin = defaults_from_type(
            PidMarginControllerConfig, conf_copy.get("margin")
        )

        if conf_copy.get("peers"):
            if conf_copy["peers"].get("defaults") and (
                peer_params := conf_copy["peers"]["defaults"].get("pid_controller")
            ):
                conf_copy["peers"]["defaults"]["pid_controller"] = PidControllerParams(
                    **peer_params
                )

            for peer in conf_copy["peers"].keys() - ["defaults"]:
                if peer_params := conf_copy["peers"][peer].get("pid_controller"):
                    conf_copy["peers"][peer]["pid_controller"] = PidControllerParams(
                        **peer_params
                    )

        self.peers = get_peers_config(PidPeerControllerConfig, conf_copy["peers"])

    def peer_config(self, pub_key: str) -> PidPeerControllerConfig:
        if not (peer_config := self.peers.get(pub_key)):
            peer_config = self.peers["default"]
        return peer_config
