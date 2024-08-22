"""
In this module two analytic controllers are defined:

EwmaController: It follows the idea of a PID Controller (Proportional,
Integral, Derivative) but it uses ewma's (exponential moving averages) as
integrals and an exponential decay function as derivative.

MrController: Is a mean reverting controller where the control variable
converges to a given level over time with a given speed. 
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from math import exp

import pytz

from .data import EwmaControllerParams, MrControllerParams


class TimeUnit(Enum):
    """Unit for most of the parameters used by the controllers."""

    SECOND = 1
    HOUR = 3600
    DAY = 24 * 3600


# If there is no timestamp when a controller was called last, this value in
# seconds is used as time delta.
DEFAULT_TIME_DELTA = 3600

# Units which are used when the controllers are initiated by EwmaControllerParans
# and MrControllerParams.
K_TIME_UNIT = TimeUnit.DAY
ALPHA_TIME_UNIT = TimeUnit.DAY


class TimeParam:
    """Defines a floating parameter which is denominated in a TimeUnit."""

    def __init__(self, value: float, time_unit: TimeUnit) -> None:
        self.value = value
        self.time_unit = time_unit

    def __ge__(self, compare: float):
        return self.value >= compare

    @property
    def to_seconds(self) -> float:
        """Converges the value to seconds."""
        return self.value / self.time_unit.value


def _lambda(alpha: TimeParam, delta_time: float):
    if alpha >= 0:
        res = exp(-alpha.to_seconds * delta_time)
    else:
        res = 1
    return res


class EwmaController:
    """
    It follows the idea of a PID Controller (Proportional, Integral, Derivative)
    but it uses ewma's (exponential moving averages) as integrals and an
    exponential decay function as derivative.
    """

    def __init__(
        self,
        k_t: TimeParam,
        k_p: TimeParam,
        k_i: TimeParam,
        k_d: float,
        alpha_d: TimeParam,
        alpha_i: TimeParam,
        error: float,
        error_ewma: float,
        error_delta_residual: float,
        control_variable: float,
        timestamp_last: datetime | None,
    ) -> None:
        self.k_t, self.k_p, self.k_i, self.k_d = k_t, k_p, k_i, k_d
        self.alpha_d, self.alpha_i = alpha_d, alpha_i
        self.shift = 0

        self.error = error
        self.error_ewma = error_ewma
        self.error_delta_residual = error_delta_residual

        self.control_variable = control_variable

        self.delta_time = 0

        self.gain_t = 0
        self.gain_p = 0
        self.gain_i = 0
        self.gain_d = 0
        self._ewma_params: EwmaControllerParams | None = None

        if timestamp_last is not None:
            self._last_time = timestamp_last.timestamp()
        else:
            self._last_time = None

    @classmethod
    def from_params(
        cls,
        ewma_controller_params: EwmaControllerParams,
        timestamp_last: datetime | None,
    ) -> EwmaController:
        """Initializes a new EwmaController by providing EwmaControllerParams"""
        return cls(
            k_t=TimeParam(ewma_controller_params.k_t, K_TIME_UNIT),
            k_p=TimeParam(ewma_controller_params.k_p, K_TIME_UNIT),
            k_d=ewma_controller_params.k_d,
            k_i=TimeParam(ewma_controller_params.k_i, K_TIME_UNIT),
            alpha_d=TimeParam(ewma_controller_params.alpha_d, ALPHA_TIME_UNIT),
            alpha_i=TimeParam(ewma_controller_params.alpha_i, ALPHA_TIME_UNIT),
            error=ewma_controller_params.error,
            error_ewma=ewma_controller_params.error_ewma,
            error_delta_residual=ewma_controller_params.error_delta_residual,
            control_variable=ewma_controller_params.control_variable,
            timestamp_last=timestamp_last,
        )

    def set_k_d(self, k_d: float) -> None:
        """Changes k_d parameter"""
        self.k_d = k_d

    def set_k_i(
        self,
        k_i: float,
    ) -> None:
        """Changes k_i parameter"""
        self.k_i = TimeParam(k_i, K_TIME_UNIT)

    def set_k_p(
        self,
        k_p: float,
    ) -> None:
        """Changes k_p parameter"""
        self.k_p = TimeParam(k_p, K_TIME_UNIT)

    def set_k_t(
        self,
        k_t: float,
    ) -> None:
        """Changes k_t parameter"""
        self.k_t = TimeParam(k_t, K_TIME_UNIT)

    def __call__(
        self,
        error: float,
        timestamp: datetime = datetime.now(pytz.utc),
    ) -> float:
        """
        Updates the error values, calculates the EWMA and adjusts the control
        variable by calling the controller.
        """

        if not self._last_time:
            dt = DEFAULT_TIME_DELTA
        elif (dt := (timestamp.timestamp() - self._last_time)) <= 0:
            raise ValueError(
                f"dt has non positive value {dt}, must be positive. "
                f"timestamp {timestamp.timestamp()}, last time {self._last_time}"
            )

        lambda_d = _lambda(self.alpha_d, dt)
        lambda_i = _lambda(self.alpha_i, dt)

        ewma = self.error_ewma * lambda_i + error * (1 - lambda_i)

        if self.alpha_i.to_seconds != 0:
            ewma_integral = error * dt + (
                self.error_ewma - error
            ) / self.alpha_i.to_seconds * (1 - lambda_i)
        else:
            ewma_integral = 0

        error_delta = error - self.error
        delta = (self.error_delta_residual + error_delta) * (1 - lambda_d)
        delta_residual = self.error_delta_residual + error_delta - delta

        self.shift = 0
        self._last_time = timestamp.timestamp()

        self.gain_t = self.k_t.to_seconds * dt
        self.gain_p = self.k_p.to_seconds * error * dt
        self.gain_i = self.k_i.to_seconds * ewma_integral
        self.gain_d = self.k_d * delta
        self.delta_time = dt
        self.error = error
        self.error_ewma = ewma
        self.error_delta_residual = delta_residual
        self.control_variable += self.gain
        self._ewma_params = None

        return self.control_variable

    @property
    def gain(self) -> float:
        """
        Returns the total controller gain compared to the last call, but without
        the a shift.
        """
        return self.gain_t + self.gain_p + self.gain_d + self.gain_i

    @property
    def ewma_params(self) -> EwmaControllerParams:
        """
        Returns the parameters of the controller as EwmaControllerParams.
        """

        # We are caching the result. The cache is reset with the next all.
        if not self._ewma_params:
            self._ewma_params = EwmaControllerParams(
                k_t=self.k_t.value,
                k_p=self.k_p.value,
                k_i=self.k_i.value,
                k_d=self.k_d,
                alpha_i=self.alpha_i.value,
                alpha_d=self.alpha_d.value,
                error=self.error,
                error_ewma=self.error_ewma,
                error_delta_residual=self.error_delta_residual,
                control_variable=self.control_variable,
            )

        return self._ewma_params

    def apply_shift(self, shift: float):
        """Adds a shift to the control variable"""
        self.shift += shift
        self.control_variable += shift


# A mean reversion controller
class MrController:
    def __init__(
        self,
        k_m: float,
        alpha: TimeParam,
        control_variable: float,
        timestamp_last: datetime | None,
    ) -> None:
        self.k_m = k_m
        self.alpha = alpha

        self.control_variable = control_variable

        self.gain_m = 0

        if timestamp_last is not None:
            self._last_time = timestamp_last.timestamp()
        else:
            self._last_time = None

        self._mr_params: MrControllerParams | None = None

    @classmethod
    def from_params(
        cls,
        mr_controller_params: MrControllerParams,
        timestamp_last: datetime | None,
    ) -> MrController:
        """Initializes a new EwmaController by providing MrControllerParams"""
        return cls(
            k_m=mr_controller_params.k_m,
            alpha=TimeParam(mr_controller_params.alpha, ALPHA_TIME_UNIT),
            control_variable=mr_controller_params.control_variable,
            timestamp_last=timestamp_last,
        )

    def set_alpha(
        self,
        alpha: float,
    ) -> None:
        """Changes k_p parameter"""
        self.alpha = TimeParam(alpha, ALPHA_TIME_UNIT)

    def __call__(
        self,
        timestamp: datetime = datetime.now(pytz.utc),
    ) -> float:
        """
        Adjusts the control variable by calling the controller.
        """

        if not self._last_time:
            dt = DEFAULT_TIME_DELTA
        elif (dt := (timestamp.timestamp() - self._last_time)) <= 0:
            raise ValueError("dt has negative value {}, must be positive".format(dt))

        lambda_m = _lambda(self.alpha, dt)

        self._last_time = timestamp.timestamp()

        self.gain_m = (self.k_m - self.control_variable) * (1 - lambda_m)

        self.delta_time = dt
        self.control_variable += self.gain
        self._mr_params = None

        return self.control_variable

    @property
    def gain(self) -> float:
        return self.gain_m

    @property
    def mr_params(self) -> MrControllerParams:
        """
        Returns the parameters of the controller as MrControllerParams.
        """
        if not self._mr_params:
            self._mr_params = MrControllerParams(
                k_m=self.k_m,
                alpha=self.alpha.value,
                control_variable=self.control_variable,
            )

        return self._mr_params
