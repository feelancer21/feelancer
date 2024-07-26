from __future__ import annotations

from .config import parse_config
from .tasks.pid.data import Pid
from .tasks.runner import TaskRunner


def app():
    with TaskRunner(parse_config()) as session:
        Pid(session)


if __name__ == "__main__":
    app()
