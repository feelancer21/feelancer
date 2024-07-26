from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from sqlalchemy.orm import Session

from feelancer.lightning.data import LightningSessionCache
from feelancer.lightning.models import DBRun

if TYPE_CHECKING:
    from feelancer.tasks.result import TaskResult
    from feelancer.tasks.session import TaskSession


class RunWriter:
    def __init__(self, task_session: TaskSession, session: Session) -> None:
        self.session = session

        self.db_run = DBRun(
            timestamp_start=task_session.timestamp_start,
            timestamp_stop=task_session.timestamp_stop,
        )

        self.ln_session = LightningSessionCache(
            task_session.ln, self.session, self.db_run
        )

    def add_all(self, results: Iterable[TaskResult]) -> None:
        for result in results:
            result.write_final_data(self.ln_session)
