from __future__ import annotations

import logging

from flufl.lock import Lock  # type: ignore

from .config import parse_config
from .log import set_logger
from .tasks.pid.data import Pid
from .tasks.runner import TaskRunner

DEFAULT_LOCK_FILENAME = "feelancer.lock"

# If the lockfile cannot be acquired in 10 seconds, an error is raised.
LOCK_TIMEOUT = 10


def app():
    lock: Lock | None = None
    try:
        config = parse_config()

        set_logger(config.get("logging"))

        # simple locking to prevent races between multiple instances.
        if not (lockfile := config.get("lockfile")):
            lockfile = DEFAULT_LOCK_FILENAME
        lock = Lock(lockfile)
        lock.lock(timeout=LOCK_TIMEOUT)  # type: ignore

        with TaskRunner(config) as session:
            Pid(session)

    except Exception:
        logging.exception("An unexpected error occurred")

    finally:
        if lock is not None and lock.is_locked:
            lock.unlock()


if __name__ == "__main__":
    app()
