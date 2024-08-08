"""
A controller based on the idea of PID. It's not a classical PID (Proportional,
Integral, Derivative) Controller, because it uses exponential moving averages
for the integral and derivative terms.
Moreover a drift term and a constant term is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import exp

from .enums import TimeUnit

DEFAULT_TIME_DIFF = 3600


@dataclass
class PidControllerParams:
    c: float = 0
    k_t: float = 0
    k_p: float = 0
    k_i: float = 0
    k_d: float = 0
    alpha_i: float = 0
    alpha_d: float = 0
    error: float = 0
    error_ewma: float = 0
    error_delta_residual: float = 0
    delta_time: float = 0
    control_factor: float = 0


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


class EwmaPID:
    """An ewma PID controller"""

    def __init__(
        self,
        c: float,
        k_t: TimeParam,
        k_p: TimeParam,
        k_i: TimeParam,
        k_d: float,
        alpha_d: TimeParam,
        alpha_i: TimeParam,
        error: float,
        error_ewma: float,
        error_delta_residual: float,
        control_factor: float,
        timestamp_last: datetime | None,
    ) -> None:
        self.c = c
        self.k_t, self.k_p, self.k_i, self.k_d = k_t, k_p, k_i, k_d
        self.alpha_d, self.alpha_i = alpha_d, alpha_i

        self._last_error = error
        self._last_ewma = error_ewma
        self._last_delta_residual = error_delta_residual

        self._last_control_factor = control_factor
        self._last_dt = 0

        self.input = 0
        self.gain_t = 0
        self.gain_p = 0
        self.gain_i = 0
        self.gain_d = 0

        if timestamp_last is not None:
            self._last_time = timestamp_last.timestamp()
        else:
            self._last_time = None

    @classmethod
    def from_pid_params(
        cls,
        pid_controller_params: PidControllerParams,
        timestamp_last: datetime | None,
        k_unit: int,
        k_time_unit: TimeUnit,
        alpha_unit: int,
        alpha_time_unit: TimeUnit,
    ) -> EwmaPID:
        return cls(
            c=pid_controller_params.c,
            k_t=TimeParam(pid_controller_params.k_t / k_unit, k_time_unit),
            k_p=TimeParam(pid_controller_params.k_p / k_unit, k_time_unit),
            k_d=pid_controller_params.k_d / k_unit,
            k_i=TimeParam(pid_controller_params.k_i / k_unit, k_time_unit),
            alpha_d=TimeParam(
                pid_controller_params.alpha_d / alpha_unit, alpha_time_unit
            ),
            alpha_i=TimeParam(
                pid_controller_params.alpha_i / alpha_unit, alpha_time_unit
            ),
            error=pid_controller_params.error,
            error_ewma=pid_controller_params.error_ewma,
            error_delta_residual=pid_controller_params.error_delta_residual,
            control_factor=pid_controller_params.control_factor,
            timestamp_last=timestamp_last,
        )

    def __call__(
        self,
        error: float,
        timestamp: datetime = datetime.now(),
    ) -> float:
        if not self._last_time:
            dt = DEFAULT_TIME_DIFF
        elif (dt := (timestamp.timestamp() - self._last_time)) <= 0:
            raise ValueError("dt has negative value {}, must be positive".format(dt))

        lambda_d = _lambda(self.alpha_d, dt)
        lambda_i = _lambda(self.alpha_i, dt)

        ewma = self._last_ewma * lambda_i + error * (1 - lambda_i)

        try:
            ewma_integral = error * dt + (
                self._last_ewma - error
            ) / self.alpha_i.to_seconds * (1 - lambda_i)
        except ZeroDivisionError:
            ewma_integral = 0

        self._last_control_factor += self.gain

        error_delta = error - self._last_error
        delta = (self._last_delta_residual + error_delta) * (1 - lambda_d)

        self._last_time = timestamp.timestamp()
        self._last_error = error
        self._last_ewma = ewma
        self._last_delta_residual += error_delta - delta
        self._last_dt = dt

        self.gain_t = self.k_t.to_seconds * dt
        self.gain_p = self.k_p.to_seconds * error * dt
        self.gain_i = self.k_i.to_seconds * ewma_integral
        self.gain_d = self.k_d * delta

        return self.control_variable

    @property
    def control_factor(self) -> float:
        return self._last_control_factor + self.gain

    @property
    def control_variable(self) -> float:
        return self.control_factor

    @property
    def control_variable_last(self) -> float:
        return self._last_control_factor

    def set_control_variable_last(self, control_variable_last: float) -> None:
        self._last_control_factor = control_variable_last

    @property
    def gain(self) -> float:
        return self.c + self.gain_t + self.gain_p + self.gain_d + self.gain_i

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
