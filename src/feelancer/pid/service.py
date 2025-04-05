from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from feelancer.log import getLogger
from feelancer.tasks.runner import RunnerRequest, RunnerResult

from .controller import PidController
from .data import PidStore

if TYPE_CHECKING:
    # from feelancer.data.db import FeelancerDB
    from feelancer.lightning.data import LightningStore

    from .data import PidConfig

logger = getLogger(__name__)


class PidService:

    def __init__(
        self,
        ln_store: LightningStore,
        pid_store: PidStore,
        get_pid_config: Callable[..., PidConfig | None],
    ):

        self._ln_store = ln_store
        self._pid_store = pid_store
        self._get_pid_config: Callable[..., PidConfig | None] = get_pid_config
        self._pid_controller: PidController | None = None

    def run(self, request: RunnerRequest) -> RunnerResult:
        """
        Runs the the pid model.
        """

        pid_config = self._get_pid_config()
        if pid_config is None:
            return RunnerResult(None, None)

        logger.info("Running pid controller...")

        if not self._pid_controller:
            self._pid_controller = PidController(
                self._pid_store, self._ln_store, pid_config
            )

        self._pid_controller(pid_config, request.ln, request.timestamp)

        res = RunnerResult(
            self._pid_controller.store_data, self._pid_controller.policy_proposals()
        )

        logger.info("Finished pid controller")
        return res

    def reset(self) -> None:
        """
        Resets the subserver
        """

        self._pid_controller = None
        logger.debug("Finished reset of pid controller")
