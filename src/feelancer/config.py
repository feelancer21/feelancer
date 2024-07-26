from __future__ import annotations

from dataclasses import dataclass

from .utils import GenericConf, get_peers_config


@dataclass
class FeelancerPeersConfig(GenericConf):
    min_seconds: int = 86400
    fee_rate_min: int = 0
    fee_rate_max: int = 2500
    inbound_fee_rate_min: int = -2500
    inbound_fee_rate_max: int = 2500
    fee_rate_ppm_min_up: int = 10
    fee_rate_ppm_min_down: int = 10
    inbound_fee_rate_ppm_min_up: int = 10
    inbound_fee_rate_ppm_min_down: int = 10


class FeelancerConfig:
    def __init__(self, config_dict: dict):
        if not (config_feelancer := config_dict.get("feelancer")):
            raise ValueError("'feelancer' section missing in configuration")

        if not (seconds := config_feelancer.get("seconds")):
            raise ValueError("'feelancer.seconds' missing in configuration")
        else:
            self.seconds = int(seconds)

        self.peers = get_peers_config(
            FeelancerPeersConfig, config_feelancer.get("peers")
        )

        self.tasks_config: dict[str, dict] = {}

        tasks = ["pid"]
        for task in tasks:
            if not (task_config := config_dict.get(task)):
                raise ValueError(f"'{task}' section missing in configuration")
            else:
                self.tasks_config[task] = task_config

    def peer_config(self, pub_key: str) -> FeelancerPeersConfig:
        if not (peer_config := self.peers.get(pub_key)):
            peer_config = self.peers["default"]
        return peer_config
