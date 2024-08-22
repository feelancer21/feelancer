"""
Definition of the two factor controllers using the PID
1. SpreadController: For 

"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Generator

from feelancer.lightning.chan_updates import PolicyProposal

from .aggregator import ChannelAggregator
from .analytics import EwmaController, MrController
from .data import (
    PidStore,
    convert_to_margin_controller,
    convert_to_pid_result,
    convert_to_pid_run,
    convert_to_spread_controller,
)

if TYPE_CHECKING:

    from feelancer.data.db import FeelancerDB
    from feelancer.lightning.chan_updates import PolicyProposal
    from feelancer.lightning.client import Channel
    from feelancer.lightning.data import LightningCache, LightningSessionCache

    from .aggregator import ChannelCollection
    from .data import EwmaControllerParams, MrControllerParams, PidConfig
    from .models import DBPidMarginController, DBPidResult, DBPidSpreadController


PEER_TARGET_UNIT = 1_000_000
LOG_THRESHOLD = 10


class ReinitRequired(Exception):
    def __init__(self, message="Reinit of FactorController required"):
        self.message = message
        super().__init__(self.message)


class MarginController:
    def __init__(
        self,
        mr_params: MrControllerParams,
        timestamp_last: datetime | None,
    ) -> None:
        self.mr_controller = MrController.from_params(
            mr_params,
            timestamp_last,
        )

        # If there is no provided timestamp and the control variable equals 0,
        # we assume that it is a new controller.
        # In this case we set the control variable to k_m.
        if not timestamp_last and mr_params.control_variable == 0:
            self.mr_controller.control_variable = mr_params.k_m

    def __call__(self, timestamp: datetime, mr_params: MrControllerParams) -> None:
        # Checking if a parameter has changed since the last run.
        m = self.mr_controller.mr_params
        if m.k_m != mr_params.k_m:
            self.mr_controller.k_m = mr_params.k_m

        if m.alpha != mr_params.alpha:
            self.mr_controller.set_alpha(mr_params.alpha)

        self.mr_controller(timestamp)

    @property
    def margin(self) -> float:
        """
        Returns the margin of the controller after the latest call.
        """
        return self.mr_controller.control_variable


class SpreadController:
    def __init__(
        self,
        ewma_params: EwmaControllerParams,
        timestamp_last: datetime | None,
    ):
        self.target = 0
        self._channel_collection: ChannelCollection | None = None

        self.ewma_controller = EwmaController.from_params(
            ewma_params,
            timestamp_last,
        )

    def __call__(
        self,
        timestamp: datetime,
        channel_collection: ChannelCollection,
        ewma_params: EwmaControllerParams,
        target: float,
        # The margin for the case we have to recalculate the spread because
        # the external fee rate had changed. This margin has to be consistent
        # with the last call of the controller.
        margin: float,
    ) -> None:
        # ewma_params can be different from the last call.
        # Changes have to be handled in following way:
        #    1. k_ changed => We only have to set the new params
        #    2. alpha_ changed =>  Reinit the controller with the historic errors

        e = self.ewma_controller.ewma_params

        if not (e.alpha_d == ewma_params.alpha_d and e.alpha_i == ewma_params.alpha_i):
            raise ReinitRequired

        # We have to update the k values if necessary.
        if e.k_d != ewma_params.k_d:
            self.ewma_controller.set_k_d(ewma_params.k_d)

        if e.k_i != ewma_params.k_i:
            self.ewma_controller.set_k_i(ewma_params.k_i)

        if e.k_p != ewma_params.k_p:
            self.ewma_controller.set_k_p(ewma_params.k_p)

        if e.k_t != ewma_params.k_t:
            self.ewma_controller.set_k_t(ewma_params.k_t)

        liquidity_out = channel_collection.liquidity_out
        liquidity_in = channel_collection.liquidity_in

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

        # If the fee_rate of the channel has changed due to manual interventions
        # outside of the controller, we have to reset the control_variable.
        # Otherwise the manual intervention will be overwritten by the controller.
        if channel_collection.ref_fee_rate_changed:
            self.ewma_controller.control_variable = (
                channel_collection.ref_fee_rate - margin
            )

        # Now we are able to call the actual ewma controller
        self.ewma_controller(error, timestamp)

        self.target = target
        self._channel_collection = channel_collection

    def channels(self) -> Generator[Channel, None, None]:
        if not self._channel_collection or not self.spread:
            return None

        for channel in self._channel_collection.pid_channels():
            yield channel

    @property
    def spread(self) -> float:
        """
        Returns the spread of the controller after the latest call.
        """
        return self.ewma_controller.control_variable

    @classmethod
    def from_history(
        cls,
        ewma_params: EwmaControllerParams,
        history: list[tuple[datetime, EwmaControllerParams, float]],
    ):
        if len(history) == 0:
            return cls(ewma_params, None)

        timestamp_init = history[0][0] - timedelta(seconds=history[0][2])
        controller = cls(ewma_params, timestamp_init)

        for timestamp, params, _ in history:
            controller.ewma_controller(params.error, timestamp)

        controller.ewma_controller.control_variable = (
            params.control_variable  # pyright: ignore - cannot be unbound because we returned early
        )

        return controller


@dataclass
class PidResult:
    channel: Channel
    margin_base: float
    margin_idiosyncratic: float
    spread: float


def yield_pid_results(
    margin_controller: MarginController,
    spread_controller: SpreadController,
    margin_idiosyncratic: float,
) -> Generator[PidResult, None, None]:
    """
    Yields the pid results per channel for a pair of margin controller and
    spread controller.
    """

    for channel in spread_controller.channels():
        yield PidResult(
            channel=channel,
            margin_base=margin_controller.margin,
            spread=spread_controller.spread,
            margin_idiosyncratic=margin_idiosyncratic,
        )

    pass


def convert_to_policy_proposal(
    pid_result: PidResult, set_inbound: bool
) -> PolicyProposal:
    """
    Converts the PidResult to a PolicyProposal

    The outbound fee rate is set to the sum of spread and margins. The margin
    consists of two parts: A specific addon (margin_idiosyncratic) for this peer
    and a base line which is determined by the margin controller.
    """

    fee_rate_ppm = (
        pid_result.margin_base + pid_result.margin_idiosyncratic + pid_result.spread
    )

    inbound_fee_rate_ppm = int(-pid_result.spread) if set_inbound else None

    return PolicyProposal(
        channel=pid_result.channel,
        fee_rate_ppm=int(max(fee_rate_ppm, 0)),
        inbound_fee_rate_ppm=inbound_fee_rate_ppm,
    )


class PidController:
    def __init__(self, db: FeelancerDB, config: PidConfig, pubkey_local: str) -> None:
        self.config = config
        self.store = PidStore(db, pubkey_local)
        self.pubkey_local = pubkey_local

        last_pid_run = self.store.last_pid_run()
        if not last_pid_run:
            self.last_timestamp = None
        else:
            self.last_timestamp = last_pid_run.run.timestamp_start

        # Fetching the last mean reversion params from db. If it is None we use
        # the current config. In this case we set the control variable to k_m,
        # because we don't want to start with a margin of 0.
        # The params are updated again with the current config in the calling
        # part of the controller.
        mr_params = self.store.last_mr_params(last_pid_run)
        if not mr_params:
            mr_params = self.config.margin.mr_controller

        self.margin_controller = MarginController(mr_params, self.last_timestamp)

        last_ewma_params = self.store.last_ewma_params(last_pid_run)
        self.spread_controller_map: dict[str, SpreadController] = {}
        for pub_key, params in last_ewma_params.items():
            self.spread_controller_map[pub_key] = SpreadController(
                params, self.last_timestamp
            )

    def __call__(
        self, config: PidConfig, ln: LightningCache, timestamp_start: datetime
    ) -> None:
        last_pid_run = self.store.last_pid_run()
        self.config = config

        # We need the last margin later we have to recalculate the spread for
        # one peer
        margin_last = self.margin_controller.margin

        # Calling the margin controller
        self.margin_controller(timestamp_start, self.config.margin.mr_controller)
        logging.debug(
            f"Called margin controller with args: timestamp {timestamp_start}; "
            f"params {self.config.margin.mr_controller}; result margin: "
            f"{self.margin_controller.margin}"
        )

        block_height = ln.lnclient.block_height
        if not last_pid_run:
            last_policies_end = {}
        else:
            last_ln_run = self.store.last_ln_run()
            last_policies_end = self.store.last_policies_end(last_ln_run)

        aggregator = ChannelAggregator.from_channels(
            config=self.config,
            policies_last=last_policies_end,
            block_height=block_height,
            channels=ln.channels.values(),
        )

        pub_keys_current = []
        for pub_key, channel_collection in aggregator.pid_collections():
            pub_keys_current.append(pub_key)

            spread_controller = self.spread_controller_map.get(pub_key)
            peer_config = self.config.peer_config(pub_key)

            # If there is no existing controller we have to create one
            if not spread_controller:
                # We check if there was controller with this peer in the past, we
                # use this params at starting point for the control variable.
                timestamp, params = self.store.last_spread_controller_params(pub_key)

                # Fallback to config if there is no controller.
                if not params:
                    params = peer_config.ewma_controller

                # Fallback to config if the controller is too old.
                if timestamp:
                    delta_hours = (timestamp_start - timestamp).total_seconds() / 3600
                    if delta_hours > config.max_age_spread_hours:
                        params = peer_config.ewma_controller

                spread_controller = self.spread_controller_map[pub_key] = (
                    SpreadController(
                        params,
                        self.last_timestamp,
                    )
                )

            # Now we have a spread controller for each peer and we can prepare
            # the call of the controller. We set the arguments for the call first.
            target = peer_config.target or aggregator.target_default
            margin_peer = margin_last + peer_config.margin_idiosyncratic
            call_args = (
                timestamp_start,
                channel_collection,
                peer_config.ewma_controller,
                target,
                margin_peer,
            )

            try:
                spread_controller(*call_args)
            except ReinitRequired:
                logging.info(f"Reinit required for {pub_key}")
                history = self.store.historic_ewma_params(pub_key)
                spread_controller = self.spread_controller_map[pub_key] = (
                    SpreadController.from_history(peer_config.ewma_controller, history)
                )
                spread_controller(*call_args)

            logging.debug(
                f"Called spread controller for {pub_key} with args: "
                f"timestamp {timestamp_start}; params {peer_config.ewma_controller}; "
                f"target {target}; margin peer {margin_peer}; result spread: "
                f"{spread_controller.spread}"
            )
        # If the channels with a peer has been closed, we can remove the
        # controller from the map.
        for pub_key in self.spread_controller_map.keys():
            if pub_key not in pub_keys_current:
                del self.spread_controller_map[pub_key]

        if pin_peer := config.pin_peer:
            pin_controller = self.spread_controller_map[pin_peer]
            peer_config = config.peer_config(pin_peer)

            shift = 0
            if config.pin_method == "fee_rate":
                shift = config.pin_value - (
                    self.margin_controller.margin
                    + peer_config.margin_idiosyncratic
                    + pin_controller.spread
                )
            elif config.pin_method == "spread":
                shift = config.pin_value - pin_controller.spread

            logging.info(f"Shifting spread controllers by {shift}")
            for c in self.spread_controller_map.values():
                c.ewma_controller.apply_shift(shift)

        self.last_timestamp = timestamp_start

    def store_data(self, ln_session: LightningSessionCache) -> None:
        ln_session.set_channel_policies(0, True)
        ln_session.set_channel_policies(0, False)
        ln_session.set_channel_policies(1, True)

        """
        We'd like log bigger changes of the fee rates to find them faster.
        Therefore we are looping of the intersection of both chan_ids and
        determine the differences on channel level.
        TODO: This can be removed in a later stage of the project.
        """
        channels_0 = ln_session.ln.get_channels(0)
        channels_1 = ln_session.ln.get_channels(1)

        for chan_id in channels_0.keys() & channels_1.keys():
            p_0 = channels_0[chan_id].policy_local
            p_1 = channels_1[chan_id].policy_local
            if not p_0 or not p_1:
                continue

            if abs(p_1.fee_rate_ppm - p_0.fee_rate_ppm) >= LOG_THRESHOLD:
                logging.warning(
                    f"fee rate on channel {chan_id} changed from "
                    f"{p_0.fee_rate_ppm} to {p_1.fee_rate_ppm}"
                )
            if (
                abs(p_1.inbound_fee_rate_ppm - p_0.inbound_fee_rate_ppm)
                >= LOG_THRESHOLD
            ):
                logging.warning(
                    f"inbound fee rate on channel {chan_id} changed from "
                    f"{p_0.inbound_fee_rate_ppm} to {p_1.inbound_fee_rate_ppm}"
                )

        ln_session.channel_liquidity

        ln_session.db_session.add(ln_session.ln_run)
        ln_session.db_session.add_all(self._yield_results(ln_session))

    def _yield_results(
        self, ln_session: LightningSessionCache
    ) -> Generator[
        DBPidMarginController | DBPidSpreadController | DBPidResult, None, None
    ]:
        pid_run = convert_to_pid_run(ln_session.db_run, ln_session.ln_node)
        yield convert_to_margin_controller(pid_run, self.margin_controller)

        for pub_key, spread_controller in self.spread_controller_map.items():
            peer = ln_session.get_channel_peer(pub_key)

            yield convert_to_spread_controller(spread_controller, peer, pid_run)

            peer_config = self.config.peer_config(pub_key)
            margin_idio = peer_config.margin_idiosyncratic
            for res in yield_pid_results(
                self.margin_controller, spread_controller, margin_idio
            ):
                channel = ln_session.get_channel_static(res.channel)
                yield convert_to_pid_result(res, channel, pid_run)

    def policy_proposals(self) -> list[PolicyProposal]:
        res = []
        if self.config.db_only:
            return res

        set_inbound = self.config.set_inbound

        for pub_key, spread_controller in self.spread_controller_map.items():
            peer_config = self.config.peer_config(pub_key)
            margin_idio = peer_config.margin_idiosyncratic
            for r in yield_pid_results(
                self.margin_controller, spread_controller, margin_idio
            ):
                res.append(convert_to_policy_proposal(r, set_inbound))

        return res
