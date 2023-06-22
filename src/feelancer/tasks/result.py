from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generator

from feelancer.lightning.chan_updates import PolicyRecommendation
from feelancer.lightning.data import LightningSessionCache


class TaskResult(ABC):
    @abstractmethod
    def write_final_data(self, ln_session: LightningSessionCache) -> None:
        pass

    @abstractmethod
    def policy_recommendations(
        self,
    ) -> Generator[PolicyRecommendation | None, None, None]:
        pass
