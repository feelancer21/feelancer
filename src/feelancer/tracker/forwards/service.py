from feelancer.tasks.runner import RunnerRequest, RunnerResult
from feelancer.tracker.proto import TrackerBaseService


class FwdtrackConfig:
    def __init__(self, config_dict: dict) -> None: ...


class FwdtrackService(TrackerBaseService[FwdtrackConfig]):

    def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult()
