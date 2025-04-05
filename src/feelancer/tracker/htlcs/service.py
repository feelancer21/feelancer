from feelancer.tasks.runner import RunnerRequest, RunnerResult
from feelancer.tracker.proto import TrackerBaseService


class HtlctrackConfig:
    def __init__(self, config_dict: dict) -> None: ...


class HtlctrackService(TrackerBaseService[HtlctrackConfig]):

    def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult()
