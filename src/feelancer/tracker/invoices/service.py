from feelancer.tasks.runner import RunnerRequest, RunnerResult
from feelancer.tracker.proto import TrackerBaseService


class InvtrackConfig:
    def __init__(self, config_dict: dict) -> None: ...


class InvtrackService(TrackerBaseService[InvtrackConfig]):

    def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult()
