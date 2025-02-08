from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from feelancer.tasks.runner import RunnerRequest, RunnerResult

from .controller import PidController

if TYPE_CHECKING:
    from feelancer.data.db import FeelancerDB

    from .data import PidConfig


class PidService:

    def __init__(self, db: FeelancerDB, get_pid_config: Callable[..., PidConfig]):

        # TODO: Init store here and init the controller with callables
        self.db = db
        self.get_pid_config: Callable[..., PidConfig] = get_pid_config
        self.pid_controller: PidController | None = None

    def run(self, request: RunnerRequest) -> RunnerResult:
        """
        Runs the the pid model.
        """

        logging.info("Running pid controller...")

        pid_config = self.get_pid_config()

        if not self.pid_controller:
            self.pid_controller = PidController(
                self.db, pid_config, request.ln.pubkey_local
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
