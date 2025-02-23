from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from feelancer.lightning.data import LightningStore
from feelancer.tasks.runner import RunnerRequest, RunnerResult

from .controller import PidController
from .data import PidStore

if TYPE_CHECKING:
    from feelancer.data.db import FeelancerDB

    from .data import PidConfig


class PidService:

    def __init__(
        self,
        db: FeelancerDB,
        pubkey_local: str,
        get_pid_config: Callable[..., PidConfig | None],
    ):

        self.pid_store = PidStore(db, pubkey_local)
        self.ln_store = LightningStore(db, pubkey_local)
        self.get_pid_config: Callable[..., PidConfig | None] = get_pid_config
        self.pid_controller: PidController | None = None

    def run(self, request: RunnerRequest) -> RunnerResult:
        """
        Runs the the pid model.
        """

        logging.info("Running pid controller...")

        pid_config = self.get_pid_config()
        if pid_config is None:
            return RunnerResult(None, None)

        if not self.pid_controller:
            self.pid_controller = PidController(
                self.pid_store, self.ln_store, pid_config
            )

        self.pid_controller(pid_config, request.ln, request.timestamp)

        res = RunnerResult(
            self.pid_controller.store_data, self.pid_controller.policy_proposals()
        )

        logging.info("Finished pid controller")
        return res

    def reset(self) -> None:
        """
        Resets the subserver
        """

        self.pid_controller = None
        logging.debug("Finished reset of pid controller")
