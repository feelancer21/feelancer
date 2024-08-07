"""
Definition of the two factor controllers using the PID
1. PeerController: For 

"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Generator

from feelancer.lightning.chan_updates import PolicyRecommendation

from .enums import TimeUnit
from .ewma_pid import EwmaPID, PidControllerParams

if TYPE_CHECKING:
    from .aggregator import ChannelAggregator, ChannelCollection


PEER_TARGET_UNIT = 1_000_000
K_UNIT = 100
K_TIME_UNIT = TimeUnit.DAY
ALPHA_UNIT = 1
ALPHA_TIME_UNIT = TimeUnit.HOUR


class ReinitRequired(Exception):
    def __init__(self, message="Reinit of FactorController required"):
        self.message = message
        super().__init__(self.message)


class FactorController:
    def __init__(
        self,
        pid_controller_params: PidControllerParams,
        timestamp_last: datetime | None,
    ):
        self.ewma_pid = EwmaPID.from_pid_params(
            pid_controller_params,
            timestamp_last,
            K_UNIT,
            K_TIME_UNIT,
            ALPHA_UNIT,
            ALPHA_TIME_UNIT,
        )
        self._pid_controller_params = pid_controller_params

    @classmethod
    def from_history(
        cls,
        pid_controller_params: PidControllerParams,
        history: list[tuple[datetime, PidControllerParams]],
    ):
        if len(history) == 0:
            return cls(pid_controller_params, None)

        timestamp_init = history[0][0] - timedelta(seconds=history[0][1].delta_time)
        controller = cls(pid_controller_params, timestamp_init)

        for timestamp, pid_controller in history:
            controller.ewma_pid(pid_controller.error, timestamp)

        return controller

    def _call_factor(
        self,
        error: float,
        timestamp: datetime,
        pid_controller_params: PidControllerParams,
    ) -> None:
        # pid_controller_params can be different from the last call.
        # Changes have to be handled in following way:
        #    1. k_ changed => We only have to set the new params
        #    2. alpha_ changed =>  Reinit the controller with the historic errors
        #    3. conversion_method and shift => Recalculate the control_factor

        self._set_pid_controller_params(pid_controller_params)
        self.ewma_pid.set_conversion(
            k_conversion_method_new=pid_controller_params.conversion_method,
            shift_new=pid_controller_params.shift,
        )
        self.ewma_pid(error, timestamp)

    def _set_pid_controller_params(
        self, pid_controller_params: PidControllerParams
    ) -> None:
        if (
            self._pid_controller_params.alpha_d == pid_controller_params.alpha_d
            and self._pid_controller_params.alpha_i == pid_controller_params.alpha_i
        ):
            self._pid_controller_params = pid_controller_params
        else:
            raise ReinitRequired

    @property
    def pid_controller_params(self) -> PidControllerParams:
        return self._pid_controller_params


class MarginController(FactorController):
    def __init__(
        self,
        pid_controller_params: PidControllerParams,
        timestamp_last: datetime | None,
    ) -> None:
        super().__init__(pid_controller_params, timestamp_last)

    @classmethod
    def from_data(
        cls,
        aggregator: ChannelAggregator,
        last_timestamp: datetime | None,
        current_timestamp: datetime,
        last_pid_params: PidControllerParams | None,
        current_pid_params: PidControllerParams,
        historic_pid_params: Callable[..., list[tuple[datetime, PidControllerParams]]],
    ) -> MarginController:
        call_args = (
            current_timestamp,
            current_pid_params,
            aggregator.avg_feerate_local,
            aggregator.avg_feerate_target,
        )

        try:
            if not (pid_params_init := last_pid_params):
                pid_params_init = current_pid_params
            controller = cls(pid_params_init, last_timestamp)
            controller(*call_args)
        except ReinitRequired:
            controller = cls.from_history(current_pid_params, historic_pid_params())
            controller(*call_args)
        return controller

    def __call__(
        self,
        timestamp: datetime,
        pid_controller_params: PidControllerParams,
        feerate_local: float,
        feerate_target: float,
    ) -> None:
        self.feerate_local = feerate_local
        self.feerate_target = feerate_target
        error = self.feerate_target - self.feerate_local

        self._call_factor(error, timestamp, pid_controller_params)


class PeerController(FactorController):
    def __init__(
        self,
        pid_controller_params: PidControllerParams,
        timestamp_last: datetime | None,
    ):
        self.feerate_recommendation: float | None = None
        self._channel_collection: ChannelCollection | None = None
        super().__init__(pid_controller_params, timestamp_last)

    @classmethod
    def from_data(
        cls,
        target: float,
        init_timestamp: datetime | None,
        current_timestamp: datetime,
        init_pid_params: PidControllerParams,
        current_pid_params: PidControllerParams,
        channel_collection: ChannelCollection,
        margin_controller: MarginController,
        historic_pid_params: Callable[..., list[tuple[datetime, PidControllerParams]]],
    ) -> PeerController:
        call_args = (
            current_timestamp,
            channel_collection,
            current_pid_params,
            target,
            margin_controller,
        )

        try:
            controller = cls(init_pid_params, init_timestamp)
            controller(*call_args)
        except ReinitRequired:
            controller = cls.from_history(current_pid_params, historic_pid_params())
            controller(*call_args)
        return controller

    def __call__(
        self,
        timestamp: datetime,
        channel_collection: ChannelCollection,
        pid_controller_params: PidControllerParams,
        target: float,
        margin_controller: MarginController | None = None,
    ) -> None:
        # If the feerate of the channel has changed due to manual interventions
        # outside of the controller, we have to reset the control_variable.
        # Otherwise the manual intervention will be overwritten by the controller.

        self._channel_collection = channel_collection
        self.target = target
        self._set_control_variable(channel_collection, margin_controller)
        error = self._error(channel_collection, target)

        self._call_factor(error, timestamp, pid_controller_params)

        self.feerate_recommendation = self.ewma_pid.control_variable
        if margin_controller:
            self.feerate_recommendation += margin_controller.ewma_pid.control_variable

    def policy_recommendations(self) -> Generator[PolicyRecommendation, None, None]:
        if not self._channel_collection or not self.feerate_recommendation:
            return None

        for channel in self._channel_collection.pid_channels():
            yield PolicyRecommendation(
                channel=channel, feerate_ppm=self.feerate_recommendation
            )

    def _set_control_variable(
        self,
        channel_collection: ChannelCollection,
        margin_controller: MarginController | None,
    ) -> None:
        if not channel_collection.ref_feerate_changed:
            return None

        control_variable_new = channel_collection.ref_feerate
        if margin_controller:
            control_variable_new -= margin_controller.ewma_pid.control_variable_last
        self.ewma_pid.set_control_variable_last(control_variable_new)

    def _error(self, channel_collection: ChannelCollection, target: float) -> float:
        liquidity_out, liquidity_in = channel_collection.liquidity
        liquidity_total = liquidity_in + liquidity_out

        try:
            ratio_in = liquidity_in / liquidity_total
            set_point = target / PEER_TARGET_UNIT

            # We make a linear interpolation to have get an error with values between
            # -0.5 and 0.5
            if ratio_in >= set_point:
                error = 0.5 / (1 - set_point) * (ratio_in - set_point)
            else:
                error = 0.5 / set_point * (ratio_in - set_point)
        except ZeroDivisionError:
            error = 0
        return error
