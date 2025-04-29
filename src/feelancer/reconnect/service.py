from __future__ import annotations

from collections.abc import Callable

from feelancer.log import getLogger
from feelancer.tasks.runner import RunnerRequest, RunnerResult

from .reconnector import Reconnector

DEFAULT_MAX_BLOCKS_TO_EXPIRY = 13

logger = getLogger(__name__)


class ReconnectConfig:
    def __init__(self, config_dict: dict) -> None:
        self.max_blocks_to_expiry = DEFAULT_MAX_BLOCKS_TO_EXPIRY
        if (m := config_dict.get("max_blocks_to_expiry")) is not None:
            try:
                self.max_blocks_to_expiry = int(m)
            except Exception as e:
                raise ValueError(f"Cannot parse 'max_blocks_to_expiry': {e}")

        self.include_inactive = False
        if (m := config_dict.get("include_inactive")) is not None:
            try:
                self.include_inactive = bool(m)
            except Exception as e:
                raise ValueError(f"Cannot parse 'include_inactive': {e}")


class ReconnectService:

    def __init__(
        self,
        reconnector: Reconnector,
        get_reconnect_config: Callable[..., ReconnectConfig | None],
    ):
        self.reconnector = reconnector
        self.get_reconnect_config = get_reconnect_config

    def run(self, request: RunnerRequest) -> RunnerResult:
        """
        Reconnects the channels using the current reconnect config.
        """

        cfg = self.get_reconnect_config()

        if cfg is None:
            return RunnerResult()

        logger.info("Running reconnector...")
        logger.debug(f"reconnect config: {cfg.__dict__=}")

        self.reconnector.reconnect_channels(
            cfg.max_blocks_to_expiry, cfg.include_inactive
        )

        logger.info("Finished reconnector")

        # return an empty result to be compliant with the protocol
        return RunnerResult()
