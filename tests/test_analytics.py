from dataclasses import dataclass
from datetime import datetime, timedelta
from math import exp

import pytz

from feelancer.pid.analytics import EwmaController, MrController
from feelancer.pid.controller import SpreadController
from feelancer.pid.data import EwmaControllerParams, MrControllerParams


# data for one call of an EwmaController
@dataclass
class EwmaCall:
    time_delta: int
    error: float


# initializes an ewma controller with a given start value and calls it
# multiple times.
def call_ewma(
    start: float, ewma_calls: list[EwmaCall], params: EwmaControllerParams
) -> EwmaController:
    time = datetime.now(pytz.utc)

    pid = EwmaController.from_params(
        ewma_controller_params=params,
        timestamp_last=time,
    )

    pid.control_variable = start

    for call in ewma_calls:
        time += timedelta(seconds=call.time_delta)
        pid(call.error, time)

    return pid


# initializes a spread controller with the .from_history method and calls it
# multiple times
def call_spread_from_history(
    start: float, ewma_calls: list[EwmaCall], params: EwmaControllerParams
) -> EwmaController:
    time = datetime.now(pytz.utc)

    iargs = (2.1, 2.1, 2.1, 2.1, 2.1, 2.1, 2.1, 2.1, 2.1, 2.1)
    history: list[tuple[datetime, EwmaControllerParams, float]] = []

    for call in ewma_calls:
        time += timedelta(seconds=call.time_delta)
        e = EwmaControllerParams(*iargs)
        e.error = call.error
        history.append((time, e, call.time_delta))

    history[-1][1].control_variable = start
    spread_ct = SpreadController.from_history(params, history)

    return spread_ct.ewma_controller


# initializes a mean reversion controller with a given start value and calls it
# multiple times.
def call_mr(
    start: float, time_delta_sec: list[int], params: MrControllerParams
) -> float:
    time = datetime.now(pytz.utc)

    mr = MrController.from_params(
        mr_controller_params=params,
        timestamp_last=time,
    )

    mr.control_variable = start

    for dt in time_delta_sec:
        time += timedelta(seconds=dt)
        mr(time)

    return mr.control_variable


# Running tests for ewma controller
def test_ewma_1():
    params = EwmaControllerParams(
        k_t=10,
        k_p=120,
        k_i=480,
        k_d=240,
        alpha_d=1.0 * 24,
        alpha_i=0.04 * 24,
    )

    ewma_calls = [
        EwmaCall(3600, 0),
        EwmaCall(7200, -1 / 8),
        EwmaCall(10800, -3 / 8),
        EwmaCall(14400, -4 / 8),
        EwmaCall(18000, -4 / 8),
        EwmaCall(18000, -2 / 8),
        EwmaCall(50400, -2 / 8),
        EwmaCall(3600, 0),
        EwmaCall(529200, 0),
    ]

    start = 1500
    expected_cv = 1310.5153013107715

    # We call the controller and compare the result with an external calculated
    # value
    ewma_ct = call_ewma(start, ewma_calls, params)
    assert ewma_ct.control_variable == expected_cv

    # We init a SpreadController from history and assert if it returns the same
    # value as spread
    spread_ect = call_spread_from_history(expected_cv, ewma_calls, params)

    assert ewma_ct.error == spread_ect.error
    assert ewma_ct.error_ewma == spread_ect.error_ewma
    assert ewma_ct.error_delta_residual == spread_ect.error_delta_residual
    assert ewma_ct.control_variable == spread_ect.control_variable


# Direct calculation of the outcome of the mean reversion controller using the
# solution of the differential equation.
def expected_outcome_mr(
    start: float, end_time: float, params: MrControllerParams
) -> float:
    lambda_m = exp(-params.alpha / (3_600 * 24) * end_time)
    return params.k_m * (1 - lambda_m) + start * lambda_m


# Running tests for mean reversion controller.
def test_mr_1():
    params = MrControllerParams(k_m=50.4, alpha=0.01 * 24)

    # Time deltas in seconds between the calls of the mr controller
    time_deltas_calls = [3600, 7200, 10800, 14400, 18000, 50400, 532800]

    # Start value of the controller
    start = 420.69

    # Total time in seconds between now and the last call. it is needed for the
    # calculation of the expected outcome
    time_total = sum(time_deltas_calls)

    # Assert that the outcome of the controller equals the direct solution
    # of the differential equation
    assert call_mr(start, time_deltas_calls, params) == expected_outcome_mr(
        start, time_total, params
    )

    # We like to test that the controller converges to k_m. Therefore we add a last
    # call in 2^32 seconds.
    time_deltas_calls += [2**32]

    assert call_mr(start, time_deltas_calls, params) == params.k_m
