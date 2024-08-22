from __future__ import annotations

from datetime import datetime, timedelta
from typing import Generator

import numpy as np
import pandas as pd
from scipy.integrate import simps
from scipy.optimize import OptimizeResult, minimize

from feelancer.pid.analytics import EwmaController, EwmaControllerParams

# seed for Monte Carlo to make results reproducible
SEED = 2100000


def _gen_ewma_call_args(
    number_days: int, steps: int, time_0: datetime
) -> Generator[datetime, None, None]:
    """
    Generates the datetimes for the EwmaController calls
    """
    x = 1
    stop = number_days * 3600 * 24 + 1
    while x <= stop:
        yield time_0 + timedelta(seconds=x)
        x += steps
    pass


class EwmaSimulationResult:
    def __init__(self, days: np.ndarray, values: np.ndarray) -> None:
        self.days = days
        self.values = values
        self._df: pd.DataFrame | None = None
        self.gradient = np.gradient(self.values, self.days)

    @property
    def df(self) -> pd.DataFrame:
        if self._df is None:
            self._df = pd.DataFrame(
                {"day": self.days, "value": self.values, "gradient": self.gradient}
            )
        return self._df

    def smoothing(self, lower_bound: float, upper_bound: float) -> float:
        mask = (self.days >= lower_bound) & (self.days <= upper_bound)
        squared_gradient = self.gradient[mask] ** 2

        return float(
            np.sqrt(
                simps(squared_gradient, self.days[mask]) / (upper_bound - lower_bound)
            )
        )


class EwmaSimulator:
    def __init__(
        self,
        k_t: float,
        k_p: float,
        k_i: float,
        k_d: float,
        alpha_i: float,
        alpha_d: float,
    ) -> None:

        self.params = EwmaControllerParams(k_t, k_p, k_i, k_d, alpha_i, alpha_d)

    def simulate(
        self,
        number_days: int,
        steps: int,
        error: float,
        ewma: float,
        error_delta_resudal: float,
        error_1: float,
    ) -> EwmaSimulationResult:
        """
        Returns a List of the controller call results as tuples
        [day, control_variable]. day is the time delta between t_0 and each call.
        """

        time_0 = datetime(2021, 1, 1, 0, 0, 0)
        controller = EwmaController.from_params(self.params, time_0)
        controller.error = error
        controller.error_delta_residual = error_delta_resudal
        controller.error_ewma = ewma

        # Calling the controller and converting the result in a result object
        # for further data analysis.
        res = [(0, 0)] + [
            (((t - time_0).total_seconds()) / 24 / 3600, controller(error_1, t))
            for t in _gen_ewma_call_args(number_days, steps, time_0)
        ]
        x = np.array([pair[0] for pair in res])
        y = np.array([pair[1] for pair in res])
        return EwmaSimulationResult(days=x, values=y)

    def monte_carlo(
        self, number_days: int, steps: int, upper_bound: float, iterations: int
    ) -> list[float]:
        """
        We define a callback for the actual simulation.
        """

        def sim(error: float, ewma: float, residual: float) -> float:
            res = self.simulate(
                number_days=number_days,
                steps=steps,
                error=error,
                ewma=ewma,
                error_delta_resudal=residual,
                error_1=0.5,
            )
            return res.smoothing(0, upper_bound)

        np.random.seed(SEED)
        error_samples = np.random.uniform(-0.5, 0.5, iterations)
        ewma_samples = np.random.uniform(-0.5, 0.5, iterations)
        delta_samples = np.random.uniform(-0.5, 0.5, iterations)

        results = []
        for i in range(iterations):
            results.append(sim(error_samples[i], ewma_samples[i], delta_samples[i]))
        return results


"""
The following function not really works. I think the reason is, that gradient
of the multidimensional optimization problem cannot really be calculated
numerically, because it is very sensitive in the alpha params.
"""


def ewma_optimize(
    upper_bound: float,
    gradient: float,
    error: float,
    ewma: float,
    error_delta_resudal: float,
    error_1: float,
    method: str = "Nelder-Mead",
) -> OptimizeResult:
    """
    Finds the params which minimize the smoothing from 0 to upper_bound.
    As constraint the gradient should converge to the given parameter.
    """

    # We define a callback function for the optimizer. The gradient of the
    # EwmaController converges to G=0.5*(k_p + k_i). Hence k_p=G-2*k_i.
    def optimize(params) -> float:
        k_i, k_d, alpha_i, alpha_d = params
        sim = EwmaSimulator(
            k_t=0,
            k_p=gradient - 2 * k_i,
            k_i=k_i,
            k_d=k_d,
            alpha_i=alpha_i,
            alpha_d=alpha_d,
        )
        sim_res = sim.simulate(
            number_days=np.ceil(upper_bound),
            steps=600,
            error=error,
            ewma=ewma,
            error_delta_resudal=error_delta_resudal,
            error_1=error_1,
        )
        return sim_res.smoothing(0, upper_bound)

    initial_guess = [0, 10, 1, 1]

    return minimize(optimize, initial_guess, method=method)
