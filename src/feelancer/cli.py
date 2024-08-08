from __future__ import annotations

import logging

from .config import parse_config
from .log import set_logger
from .tasks.pid.data import Pid
from .tasks.runner import TaskRunner


def app():
    try:
        config = parse_config()
        set_logger(config.get("logging"))
        with TaskRunner(config) as session:
            Pid(session)

    except Exception:
        logging.exception("An unexpected error occurred")


if __name__ == "__main__":
    app()
