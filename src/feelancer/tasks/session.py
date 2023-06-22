from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Generator

from feelancer.lightning.chan_updates import update_channel_policies

if TYPE_CHECKING:
    from feelancer.config import FeelancerConfig
    from feelancer.data.db import FeelancerDB
    from feelancer.lightning.chan_updates import PolicyRecommendation
    from feelancer.lightning.data import LightningCache

    from .result import TaskResult


class TaskSession:
    def __init__(self, ln: LightningCache, db: FeelancerDB, config: FeelancerConfig):
        self.ln = ln
        self.db = db
        self.config = config
        self.timestamp_start = datetime.now()
        self.pubkey_local: str = self.ln.pubkey_local
        self.results: list[TaskResult] = []

    @property
    def timestamp_stop(self) -> datetime:
        return datetime.now()

    def get_task_config(self, task_name: str):
        return self.config.tasks_config[task_name]

    def add_result(self, result: TaskResult) -> None:
        self.results.append(result)

    def policy_recommendations(self) -> Generator[PolicyRecommendation, None, None]:
        for r in self.results:
            for p in r.policy_recommendations():
                if not p:
                    continue
                yield p

    def policy_updates(self) -> None:
        update_channel_policies(
            self.ln.lnclient, self.policy_recommendations(), self.config
        )

    def gen_results(self) -> Generator[TaskResult, None, None]:
        for res in self.results:
            yield res
