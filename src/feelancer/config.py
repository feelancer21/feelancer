from __future__ import annotations

import argparse
from dataclasses import dataclass

from .utils import GenericConf, get_peers_config, toml_to_dict


@dataclass
class FeelancerPeersConfig(GenericConf):
    min_seconds: int = 86400
    feerate_min_ppm_up: int = 10
    feerate_min_ppm_down: int = 10
    feerate_min: int = 0
    feerate_max: int = 2500


class FeelancerConfig:
    def __init__(self, config_dict: dict):
        if not (config_feelancer := config_dict.get("feelancer")):
            raise ValueError("'feelancer' section missing in configuration")

        if not (run_tasks := config_feelancer.get("run_tasks")):
            raise ValueError("'feelancer.run_tasks' missing in configuration")

        self.peers = get_peers_config(
            FeelancerPeersConfig, config_feelancer.get("peers")
        )

        self.tasks_config: dict[str, dict] = {}

        for task in run_tasks:
            if task in ["feelancer", "lnd", "sqlalchemy"]:
                continue

            if not (task_config := config_dict.get(task)):
                raise ValueError("'pid' section missing in configuration")
            else:
                self.tasks_config[task] = task_config

    def peer_config(self, pub_key: str) -> FeelancerPeersConfig:
        if not (peer_config := self.peers.get(pub_key)):
            peer_config = self.peers["default"]
        return peer_config


def parse_config() -> dict:
    parser = argparse.ArgumentParser(
        prog="feelancer",
        description="Adjusting fees for Lightning Channels using a PID-Controller",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Input file for reading (default: '~/.feelancer/feelancer.toml')",
        default="~/.feelancer/feelancer.toml",
    )
    args = parser.parse_args()

    return toml_to_dict(args.config)
