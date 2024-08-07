from dataclasses import dataclass
from datetime import datetime, timedelta

from feelancer.tasks.pid.ewma_pid import EwmaPID, PidControllerParams, TimeUnit

K_UNIT = 100
ALPHA_UNIT = 1


@dataclass
class EwmaCall:
    time_delta: int
    error: float
    result: float


def call_ewma(
    input: float, ewma_calls: list[EwmaCall], params: PidControllerParams
) -> float:
    time = datetime.now()

    pid = EwmaPID.from_pid_params(
        pid_controller_params=params,
        timestamp_last=time,
        k_unit=100,
        k_time_unit=TimeUnit.DAY,
        alpha_unit=1,
        alpha_time_unit=TimeUnit.HOUR,
    )

    pid.set_control_variable_last(input)

    for call in ewma_calls:
        time += timedelta(seconds=call.time_delta)
        pid(call.error, time)

    return pid.control_variable


def test_1():
    params = PidControllerParams(
        c=0,
        k_t=10,
        k_p=120,
        k_i=480,
        k_d=240,
        alpha_d=1.0,
        alpha_i=0.04,
    )
    ewma_calls = [
        EwmaCall(3600, 0, 0),
        EwmaCall(7200, -1 / 8, 0),
        EwmaCall(10800, -3 / 8, 0),
        EwmaCall(14400, -4 / 8, 0),
        EwmaCall(18000, -4 / 8, 0),
        EwmaCall(18000, -2 / 8, 0),
        EwmaCall(50400, -2 / 8, 0),
        EwmaCall(532800, 0, 0),
    ]
    assert call_ewma(1500, ewma_calls, params) == 1498.1050423388772
