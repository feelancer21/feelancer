"""
A controller based on the idea of PID. It's not a classical PID (Proportional,
Integral, Derivative) Controller, because it uses exponential moving averages
for the integral and derivative terms.
Moreover a drift term and a constant term is implemented.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from math import exp

import pytz

from .data import EwmaControllerParams, MrControllerParams


class TimeUnit(Enum):
    SECOND = 1
    HOUR = 3600
    DAY = 24 * 3600


DEFAULT_TIME_DIFF = 3600
K_TIME_UNIT = TimeUnit.DAY
ALPHA_TIME_UNIT = TimeUnit.DAY


class TimeParam:
    def __init__(self, value: float, time_unit: TimeUnit) -> None:
        self.value = value
        self.time_unit = time_unit

    def __ge__(self, compare: float):
        return self.value >= compare

    @property
    def to_seconds(self) -> float:
        return self.value / self.time_unit.value


def _lambda(alpha: TimeParam, delta_time: float):
    if alpha >= 0:
        res = exp(-alpha.to_seconds * delta_time)
    else:
        res = 1
    return res


# An ewma PID controller
class EwmaController:
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

        self._last_error = error
        self._last_ewma = error_ewma
        self._last_delta_residual = error_delta_residual

        self._last_control_variable = control_variable
        self._last_dt = 0

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
        self.k_d = k_d

    def set_k_i(
        self,
        k_i: float,
    ) -> None:
        self.k_i = TimeParam(k_i, K_TIME_UNIT)

    def set_k_p(
        self,
        k_p: float,
    ) -> None:
        self.k_p = TimeParam(k_p, K_TIME_UNIT)

    def set_k_t(
        self,
        k_t: float,
    ) -> None:
        self.k_t = TimeParam(k_t, K_TIME_UNIT)

    def __call__(
        self,
        error: float,
        timestamp: datetime = datetime.now(pytz.utc),
    ) -> float:
        if not self._last_time:
            dt = DEFAULT_TIME_DIFF
        elif (dt := (timestamp.timestamp() - self._last_time)) <= 0:
            raise ValueError("dt has negative value {}, must be positive".format(dt))

        lambda_d = _lambda(self.alpha_d, dt)
        lambda_i = _lambda(self.alpha_i, dt)

        ewma = self._last_ewma * lambda_i + error * (1 - lambda_i)

        if self.alpha_i.to_seconds != 0:
            ewma_integral = error * dt + (
                self._last_ewma - error
            ) / self.alpha_i.to_seconds * (1 - lambda_i)
        else:
            ewma_integral = 0

        error_delta = error - self._last_error
        delta = (self._last_delta_residual + error_delta) * (1 - lambda_d)
        delta_residual = self._last_delta_residual + error_delta - delta

        self._last_control_variable = self.control_variable
        self.shift = 0
        self._last_time = timestamp.timestamp()
        self._last_error = error
        self._last_ewma = ewma
        self._last_delta_residual = delta_residual
        self._last_dt = dt

        self.gain_t = self.k_t.to_seconds * dt
        self.gain_p = self.k_p.to_seconds * error * dt
        self.gain_i = self.k_i.to_seconds * ewma_integral
        self.gain_d = self.k_d * delta
        self._ewma_params = None

        return self.control_variable

    @property
    def control_variable(self) -> float:
        return self._last_control_variable + self.gain + self.shift

    @property
    def control_variable_last(self) -> float:
        return self._last_control_variable

    def set_control_variable_last(self, control_variable_last: float) -> None:
        self._last_control_variable = control_variable_last

    @property
    def gain(self) -> float:
        return self.gain_t + self.gain_p + self.gain_d + self.gain_i

    @property
    def error(self) -> float:
        return self._last_error

    @property
    def error_delta_residual(self) -> float:
        return self._last_delta_residual

    @property
    def error_ewma(self) -> float:
        return self._last_ewma

    @property
    def delta_time(self) -> float:
        return self._last_dt

    @property
    def ewma_params(self) -> EwmaControllerParams:
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

    def set_shift(self, shift: float):
        self.shift = shift


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

        self._last_control_variable = control_variable
        self._last_dt = 0

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
        self.alpha = TimeParam(alpha, ALPHA_TIME_UNIT)

    def __call__(
        self,
        timestamp: datetime = datetime.now(pytz.utc),
    ) -> float:
        if not self._last_time:
            dt = DEFAULT_TIME_DIFF
        elif (dt := (timestamp.timestamp() - self._last_time)) <= 0:
            raise ValueError("dt has negative value {}, must be positive".format(dt))

        lambda_m = _lambda(self.alpha, dt)

        self._last_control_variable = self.control_variable

        self._last_time = timestamp.timestamp()
        self._last_dt = dt

        self.gain_m = (self.k_m - self._last_control_variable) * (1 - lambda_m)

        self._mr_params = None

        return self.control_variable

    @property
    def control_variable(self) -> float:
        return self._last_control_variable + self.gain

    @property
    def control_variable_last(self) -> float:
        return self._last_control_variable

    def set_control_variable_last(self, control_variable_last: float) -> None:
        self._last_control_variable = control_variable_last

    @property
    def gain(self) -> float:
        return self.gain_m

    @property
    def delta_time(self) -> float:
        return self._last_dt

    @property
    def mr_params(self) -> MrControllerParams:
        if not self._mr_params:
            self._mr_params = MrControllerParams(
                k_m=self.k_m,
                alpha=self.alpha.value,
                control_variable=self.control_variable,
            )

        return self._mr_params
