from __future__ import annotations

import unittest
from copy import deepcopy
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
    # Seconds between the current call and the previous call.
    time_delta: int

    # error used for calling the controller
    error: float

    # params used for calling the controller. If None, we are using the previous
    # one. In context of the tests only changes in k params are implemented
    params: EwmaControllerParams | None = None

    # init a new ewma controller with the given params and the results of the
    # previous controller.
    force_reinit: bool = False

    # if set, the control variable will be overwritten before the call by this
    # value
    control_variable: float | None = None


# Testcase for calling EwmaController
@dataclass
class TCaseEwma:

    # name of the test case
    name: str

    # description of the testcase
    description: str

    # start value of the control variable for the controller
    start_value_ewma: float

    # list of calls to be executed
    calls: list[EwmaCall]

    # expected result of the control variable after the last call
    expected_control_variable: float


# Testcase for the from_history method of the SpreadRateController
@dataclass
class TCaseSpreadRateController:
    # name of the test case
    name: str

    # description of the testcase
    description: str
    start_value_ewma: float
    calls: list[EwmaCall]
    params: EwmaControllerParams

    # expected control variable
    expected_control_variable: float


# Direct calculation of the outcome of the mean reversion controller using the
# solution of the differential equation.
def expected_outcome_mr(
    start_value_ewma: float, end_time: float, params: MrControllerParams
) -> float:
    lambda_m = exp(-params.alpha / (3_600 * 24) * end_time)
    return params.k_m * (1 - lambda_m) + start_value_ewma * lambda_m


def call_ewma(start_value_ewma: float, ewma_calls: list[EwmaCall]) -> EwmaController:

    time = datetime.now(pytz.utc)
    ewma: EwmaController | None = None

    for call in ewma_calls:

        p: EwmaControllerParams | None = None
        if call.params is not None:
            p = deepcopy(call.params)

        if ewma is None and p is None:
            raise ValueError("No EwmaControllerParams for the first call provided.")

        # init a new controller if it is forced
        if ewma is not None and p is not None and call.force_reinit is True:
            p.control_variable = ewma.control_variable
            p.error = ewma.error
            p.error_delta_residual = ewma.error_delta_residual
            p.error_ewma = ewma.error_ewma
            ewma = EwmaController.from_params(
                ewma_controller_params=p,
                timestamp_last=time,
            )

        # check if k parameters have changed and return None if of alpha has changed
        # because this is isn't implemented here
        if ewma is not None and p is not None and call.force_reinit is False:
            e = ewma.ewma_params
            if e.k_d != p.k_d:
                ewma.set_k_d(p.k_d)

            if e.k_i != p.k_i:
                ewma.set_k_i(p.k_i)

            if e.k_p != p.k_p:
                ewma.set_k_p(p.k_p)

            if e.k_t != p.k_t:
                ewma.set_k_t(p.k_t)

            if not (e.alpha_d == p.alpha_d and e.alpha_i == p.alpha_i):
                raise ValueError("Change of alpha parameters not implemented.")

        # First Call
        if ewma is None and p is not None:
            p.control_variable = start_value_ewma
            ewma = EwmaController.from_params(
                ewma_controller_params=p,
                timestamp_last=time,
            )

        if ewma is None:
            raise ValueError("ewma is None.")

        if call.control_variable is not None:
            ewma.control_variable = call.control_variable

        time += timedelta(seconds=call.time_delta)

        ewma(call.error, time)  # type: ignore - cannot be None here

    return ewma  # type: ignore - cannot be None here


# initializes a spread controller with the .from_history method and calls it
# multiple times
def call_spread_from_history(
    start_value_ewma: float, ewma_calls: list[EwmaCall], params: EwmaControllerParams
) -> EwmaController:
    time = datetime.now(pytz.utc)

    # We are creating a history calls using this historic params.
    historic_params = (2.1, 2.1, 2.1, 2.1, 2.1, 2.1, 2.1, 2.1, 2.1, 2.1)
    history: list[tuple[datetime, EwmaControllerParams, float]] = []

    for call in ewma_calls:
        time += timedelta(seconds=call.time_delta)
        e = EwmaControllerParams(*historic_params)
        e.error = call.error
        history.append((time, e, call.time_delta))

    # Reset of the control variable of the last history element and initializing
    # the controller from the history with the current.
    # Hence the ewma's will be updated.
    history[-1][1].control_variable = start_value_ewma
    spread_ct = SpreadController.from_history(params, history)

    return spread_ct.ewma_controller


# initializes a mean reversion controller with a given start_value_ewma value and calls it
# multiple times.
def call_mean_reverting(
    start_value_mr: float, time_delta_sec: list[int], params: MrControllerParams
) -> float:
    time = datetime.now(pytz.utc)

    mr = MrController.from_params(
        mr_controller_params=params,
        timestamp_last=time,
    )

    mr.control_variable = start_value_mr

    for dt in time_delta_sec:
        time += timedelta(seconds=dt)
        mr(time)

    return mr.control_variable


class TestEwmaController(unittest.TestCase):

    def setUp(self):

        # Basis params.
        params_1 = EwmaControllerParams(
            k_t=10,
            k_p=120,
            k_i=480,
            k_d=240,
            alpha_d=1.0 * 24,
            alpha_i=0.04 * 24,
        )

        # doubled  k params
        params_2 = EwmaControllerParams(
            k_t=10 * 2,
            k_p=120 * 2,
            k_i=480 * 2,
            k_d=240 * 2,
            alpha_d=1.0 * 24,
            alpha_i=0.04 * 24,
        )

        # Only one init of the controller
        ewma_calls_1 = [
            EwmaCall(3600, 0, params_1),
            EwmaCall(7200, -1 / 8),
            EwmaCall(10800, -3 / 8),
            EwmaCall(14400, -4 / 8),
            EwmaCall(18000, -4 / 8),
            EwmaCall(18000, -2 / 8),
            EwmaCall(50400, -2 / 8),
            EwmaCall(3600, 0),
            EwmaCall(529200, 0),
        ]

        # Init of the controller at each call
        ewma_calls_2 = [
            EwmaCall(3600, 0, params_1, True),
            EwmaCall(7200, -1 / 8, params_1, True),
            EwmaCall(10800, -3 / 8, params_1, True),
            EwmaCall(14400, -4 / 8, params_1, True),
            EwmaCall(18000, -4 / 8, params_1, True),
            EwmaCall(18000, -2 / 8, params_1, True),
            EwmaCall(50400, -2 / 8, params_1, True),
            EwmaCall(3600, 0, params_1, True),
            EwmaCall(529200, 0, params_1, True),
        ]

        # Params changes in call 2
        ewma_calls_3 = [
            EwmaCall(3600, 0, params_1),
            EwmaCall(7200, -1 / 8, params_2),
            EwmaCall(10800, -3 / 8),
            EwmaCall(14400, -4 / 8),
            EwmaCall(18000, -4 / 8),
            EwmaCall(18000, -2 / 8),
            EwmaCall(50400, -2 / 8),
            EwmaCall(3600, 0),
            EwmaCall(529200, 0),
        ]

        self.test_cases_ewma: list[TCaseEwma] = []
        self.test_cases_ewma.append(
            TCaseEwma(
                name="1",
                description="Test ewma controller with init before first call only.",
                start_value_ewma=1500,
                calls=ewma_calls_1,
                expected_control_variable=1310.5153013107715,
            )
        )

        self.test_cases_ewma.append(
            TCaseEwma(
                name="2",
                description="Test ewma controller with reinit at every call.",
                start_value_ewma=1500,
                calls=ewma_calls_2,
                expected_control_variable=1310.5153013107715,
            )
        )

        self.test_cases_ewma.append(
            TCaseEwma(
                name="3",
                description="Test ewma controller with change in k params after first call.",
                start_value_ewma=1500,
                calls=ewma_calls_3,
                # 1500.4166666666667 is the control_variable with params_1 after first call.
                # The difference to the origin expected result is doubled now because the k params
                # have doubled.
                expected_control_variable=1500.4166666666667
                + (1310.5153013107715 - 1500.4166666666667) * 2,
            )
        )

        self.test_cases_spread: list[TCaseSpreadRateController] = []
        self.test_cases_spread.append(
            TCaseSpreadRateController(
                name="10",
                description="Test spread rate controller.",
                start_value_ewma=1500,
                calls=ewma_calls_1,
                params=params_1,
                expected_control_variable=1310.5153013107715,
            )
        )

    def test_ewma(self):
        for t in self.test_cases_ewma:
            msg = f"{t.name=}; {t.description=}"
            ewma = call_ewma(t.start_value_ewma, t.calls)
            self.assertIsInstance(ewma, EwmaController, msg)
            self.assertAlmostEqual(
                ewma.control_variable, t.expected_control_variable, 7, msg
            )

    def test_spread_rate_controller_hist(self):

        for t in self.test_cases_spread:
            msg = f"{t.name=}; {t.description=}"
            ewma = call_ewma(t.start_value_ewma, t.calls)
            self.assertIsInstance(ewma, EwmaController, msg)
            if ewma is None:
                continue

            # We init a SpreadController from history and assert if it returns the same
            # error and ewma values as the ewma controller
            spread_ct = call_spread_from_history(
                ewma.control_variable, t.calls, t.params
            )
            self.assertEqual(ewma.error, spread_ct.error, msg)
            self.assertEqual(ewma.error_ewma, spread_ct.error_ewma, msg)
            self.assertEqual(
                ewma.error_delta_residual, spread_ct.error_delta_residual, msg
            )
            self.assertEqual(ewma.control_variable, spread_ct.control_variable, msg)

    # Running tests for mean reversion controller.
    def test_mr_controller(self):
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
        self.assertEqual(
            call_mean_reverting(start, time_deltas_calls, params),
            expected_outcome_mr(start, time_total, params),
        )

        # We like to test that the controller converges to k_m. Therefore we add a last
        # call in 2^32 seconds.
        time_deltas_calls += [2**32]

        self.assertEqual(
            call_mean_reverting(start, time_deltas_calls, params), params.k_m
        )
