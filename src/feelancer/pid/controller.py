"""
Defines MarginController, SpreadController and the PidController
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from feelancer.lightning.chan_updates import PolicyProposal

from .aggregator import ChannelAggregator
from .analytics import EwmaController, MrController
from .data import (
    new_margin_controller,
    new_pid_result,
    new_pid_run,
    new_spread_controller,
)

if TYPE_CHECKING:

    from feelancer.lightning.chan_updates import PolicyProposal
    from feelancer.lightning.client import Channel
    from feelancer.lightning.data import (
        LightningCache,
        LightningSessionCache,
        LightningStore,
    )

    from .aggregator import ChannelCollection
    from .data import (
        EwmaControllerParams,
        MrControllerParams,
        PidConfig,
        PidSpreadControllerConfig,
        PidStore,
    )
    from .models import DBPidMarginController, DBPidResult, DBPidSpreadController


PEER_TARGET_UNIT = 1_000_000
LOG_THRESHOLD = 10

logger = logging.getLogger(__name__)


def _calc_error(
    liquidity_in: float,
    liquidity_out: float,
    target: float,
    error_max: float,
    error_min: float,
    ratio_error_max: int,
    ratio_error_min: int,
    name: str = "",
) -> float:
    """
    Calculates the error for EwmaController.

    The error is 0 if liquidity_in (normalized in millionths) is at the
    target. If liquidity_in is higher than the target the error is in the
    range ]0; error_max]. And if liquidity_in is lower than the target the error
    is in the range [error_min; 0[.
    """

    if ratio_error_max < ratio_error_min:
        raise ValueError(f"{ratio_error_max=} must be greater than {ratio_error_min=}.")

    if error_max < 0:
        raise ValueError(f"{error_max=} must be greater equal than 0.")

    if error_min > 0:
        raise ValueError(f"{error_min=} must be less equal than 0.")

    set_point_max = ratio_error_max / PEER_TARGET_UNIT
    set_point_min = ratio_error_min / PEER_TARGET_UNIT
    set_point = target / PEER_TARGET_UNIT

    def calculate_error(ratio: float) -> float:
        # Interpolate with piecewise linear functions between [error_min; error_max ]
        if ratio >= set_point_max:
            return error_max

        if ratio <= set_point_min:
            return error_min

        if ratio_in >= set_point:
            return error_max / (set_point_max - set_point) * (ratio - set_point)

        else:
            return -error_min / (set_point - set_point_min) * (ratio - set_point)

    liquidity_total = liquidity_in + liquidity_out
    try:
        ratio_in = liquidity_in / liquidity_total

    except ZeroDivisionError:
        error = 0
        logger.debug(f"Error calculated for {name}; {error=:.6f}")

        return error

    else:
        logger.debug(f"Ratio calculated for {name}; {ratio_in:=.6f}; {set_point=:.6f}")
        error = calculate_error(ratio_in)
        logger.debug(f"Error calculated for {name}; {error=:.6f}")

        return error


class ReinitRequired(Exception):
    def __init__(self, message="Reinit of Controller required"):
        self.message = message
        super().__init__(self.message)


class MarginController:
    """
    Controls the margin with an MrController.
    """

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
        # we assume that it is a new controller.In this case we set the control
        # variable to k_m.
        if not timestamp_last and mr_params.control_variable == 0:
            self.mr_controller.control_variable = mr_params.k_m

    def __call__(self, timestamp: datetime, mr_params: MrControllerParams) -> float:
        """Updates the parameters and calls the MrController"""

        # Checking if a parameter has changed since the last run. We are not doing
        # a Reinit if alpha changes, because it could lead to a jump in the margin
        # and hence in the fee rate too.
        m = self.mr_controller.mr_params
        if m.k_m != mr_params.k_m:
            self.mr_controller.k_m = mr_params.k_m

        if m.alpha != mr_params.alpha:
            self.mr_controller.set_alpha(mr_params.alpha)

        self.mr_controller(timestamp)

        return self.mr_controller.control_variable

    @property
    def margin(self) -> float:
        """
        Returns the margin of the controller after the latest call.
        """
        return self.mr_controller.control_variable


class SpreadController:
    """
    Controls the spread of one peer with an EwmaController.
    """

    def __init__(
        self,
        ewma_params: EwmaControllerParams,
        timestamp_last: datetime | None,
        name: str,
    ):
        self.ewma_controller = EwmaController.from_params(ewma_params, timestamp_last)

        # name is used for logging
        self._name = name

    def __call__(
        self,
        timestamp: datetime,
        liquidity_in: float,
        liquidity_out: float,
        ewma_params: EwmaControllerParams,
        target: float,
        error_max: float,
        error_min: float,
        ratio_error_max: int,
        ratio_error_min: int,
        spread_recalibrated: float | None = None,
    ) -> None:
        """Updates the parameters and calls the EwmaController"""

        # We check of the ewma params have changed since the last call. If one
        # of the alpha params changed, we are raising an error, because we have
        # to reinitialize the controller with its history to calculate a new
        # ewma.
        e = self.ewma_controller.ewma_params
        if not (e.alpha_d == ewma_params.alpha_d and e.alpha_i == ewma_params.alpha_i):
            raise ReinitRequired

        # If one the k_ changed we update the value only
        if e.k_d != ewma_params.k_d:
            self.ewma_controller.set_k_d(ewma_params.k_d)

        if e.k_i != ewma_params.k_i:
            self.ewma_controller.set_k_i(ewma_params.k_i)

        if e.k_p != ewma_params.k_p:
            self.ewma_controller.set_k_p(ewma_params.k_p)

        if e.k_t != ewma_params.k_t:
            self.ewma_controller.set_k_t(ewma_params.k_t)

        # Calculation of the error for EwmaController, it maps our inbound liquidity
        # to a value in the range [-error_min; error_max].
        error = _calc_error(
            liquidity_in=liquidity_in,
            liquidity_out=liquidity_out,
            target=target,
            error_max=error_max,
            error_min=error_min,
            ratio_error_max=ratio_error_max,
            ratio_error_min=ratio_error_min,
            name=self._name,
        )

        # Set a new spread if recalibration was needed.
        if spread_recalibrated is not None:
            logger.debug(f"Set {spread_recalibrated=:,.2f}")
            self.ewma_controller.control_variable = spread_recalibrated

        # Now we are able to call the actual ewma controller
        self.ewma_controller(error, timestamp)

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
        name: str,
    ):
        """
        Initializes a new controller with the provided ewma params and calls
        it for the whole history.
        """
        if len(history) == 0:
            return cls(ewma_params, None, name)

        timestamp_init = history[0][0] - timedelta(seconds=history[0][2])
        controller = cls(ewma_params, timestamp_init, name)

        for timestamp, params, _ in history:
            controller.ewma_controller(params.error, timestamp)

        controller.ewma_controller.control_variable = (
            params.control_variable  # pyright: ignore - cannot be unbound because we returned early
        )

        return controller


@dataclass
class PidChannelResult:
    channel: Channel
    margin_base: float
    margin_idiosyncratic: float
    spread: float

    def _margin_total(self) -> float:
        """
        Returns the total margin for this channel.
        It is the sum of the base margin and the idiosyncratic margin.
        """
        return self.margin_base + self.margin_idiosyncratic

    def get_policy_proposal(
        self, set_inbound: bool, force_update: bool
    ) -> PolicyProposal:
        """
        Converts the PidChannelResult to a PolicyProposal

        The outbound fee rate is set to the sum of spread and margin.
        """

        fee_rate_ppm = self._margin_total() + self.spread

        inbound_fee_rate_ppm = int(-self.spread) if set_inbound else None

        return PolicyProposal(
            channel=self.channel,
            fee_rate_ppm=int(max(fee_rate_ppm, 0)),
            inbound_fee_rate_ppm=inbound_fee_rate_ppm,
            force_update=force_update,
        )


@dataclass
class PidPeerResult:
    """
    Result class of the PidController for one peer.
    """

    spread_controller: SpreadController
    target: float
    channel_collection: ChannelCollection
    config: PidSpreadControllerConfig

    def _channels(self) -> Generator[Channel]:
        """
        Yields all channels associated with this peer result.
        """

        yield from self.channel_collection.pid_channels()

    @property
    def margin_idiosyncratic(self) -> float:
        """
        Returns the idiosyncratic margin for this peer.
        """

        res = (
            self.config.margin_idiosyncratic
            + (self.config.margin_idiosyncratic_pct * self.spread_controller.spread)
            / 100
        )

        if res < self.config.margin_idiosyncratic_min:
            return self.config.margin_idiosyncratic_min

        if res > self.config.margin_idiosyncratic_max:
            return self.config.margin_idiosyncratic_max

        return res

    def yield_pid_channel_results(
        self,
        margin_base: float,
    ) -> Generator[PidChannelResult]:
        """
        Yields the pid results per channel for a pair of margin controller and
        spread controller.
        """

        for channel in self._channels():
            yield PidChannelResult(
                channel=channel,
                margin_base=margin_base,
                spread=self.spread_controller.spread,
                margin_idiosyncratic=self.margin_idiosyncratic,
            )


@dataclass
class PidResult:
    """
    Result class of the PidController.
    """

    config: PidConfig
    peer_result_map: dict[str, PidPeerResult]
    margin_controller: MarginController
    # Peers which require a force with the next policy update. Can be peers
    # with new channels or peers with a change of the reference fee rate.
    peers_update_force: set[str]

    def store_data(self, ln_session: LightningSessionCache) -> None:
        """
        Adds all relevant date of the PidController and a LightningCache
        to a db session.
        """

        ln_session.channel_policies(0, True)
        ln_session.channel_policies(0, False)
        ln_session.channel_policies(1, True)

        # We'd like log bigger changes of the fee rates to find them faster.
        # Therefore we are looping of the intersection of both chan_ids and
        # determine the differences on channel level.
        # TODO: This can be removed in a later stage of the project.
        channels_0 = ln_session.ln.channels_by_sequence(0)
        channels_1 = ln_session.ln.channels_by_sequence(1)

        for chan_id in channels_0.keys() & channels_1.keys():
            p_0 = channels_0[chan_id].policy_local
            p_1 = channels_1[chan_id].policy_local
            if not p_0 or not p_1:
                continue

            if abs(p_1.fee_rate_ppm - p_0.fee_rate_ppm) >= LOG_THRESHOLD:
                logger.warning(
                    f"fee rate on channel {chan_id} changed from "
                    f"{p_0.fee_rate_ppm} to {p_1.fee_rate_ppm}"
                )
            if (
                abs(p_1.inbound_fee_rate_ppm - p_0.inbound_fee_rate_ppm)
                >= LOG_THRESHOLD
            ):
                logger.warning(
                    f"inbound fee rate on channel {chan_id} changed from "
                    f"{p_0.inbound_fee_rate_ppm} to {p_1.inbound_fee_rate_ppm}"
                )

        ln_session.channel_liquidity

        ln_session.db_session.add(ln_session.ln_run)
        ln_session.db_session.add_all(self._yield_results(ln_session))

    def _yield_results(
        self, ln_session: LightningSessionCache
    ) -> Generator[DBPidMarginController | DBPidSpreadController | DBPidResult]:
        """
        Generates all sqlalchemy objects with the results of the last call of
        the PidController.
        """

        pid_run = new_pid_run(ln_session.db_run, ln_session.ln_node)
        yield new_margin_controller(pid_run, self.margin_controller)

        for pub_key, r in self.peer_result_map.items():
            peer = ln_session.channel_peer_by(pub_key=pub_key)

            yield new_spread_controller(r.spread_controller, r.target, peer, pid_run)

            for c in r.yield_pid_channel_results(self.margin_controller.margin):
                channel = ln_session.channel_static_by(channel=c.channel)
                yield new_pid_result(c, channel, pid_run)

    def policy_proposals(self) -> list[PolicyProposal]:
        """
        Creates a list of all PolicyProposals with the results of the last call of
        the PidController.
        """

        res = []
        set_inbound = self.config.set_inbound

        for pub_key, r in self.peer_result_map.items():
            # Skipping the peer when no policy updates are required.
            if self.config.db_only and pub_key not in self.config.no_db_only_pubkeys:
                continue

            for c in r.yield_pid_channel_results(self.margin_controller.margin):
                force_update = pub_key in self.peers_update_force
                res.append(c.get_policy_proposal(set_inbound, force_update))

        return res


class PidController:
    """
    The main controller which holds the MarginController and all SpreadControllers.
    """

    def __init__(
        self, pid_store: PidStore, ln_store: LightningStore, config: PidConfig
    ) -> None:

        self.pid_store = pid_store
        self.ln_store = ln_store

        # Fetching the last timestamp from the database
        last_run_id, self.last_timestamp = self.pid_store.pid_run_last()

        # Fetching the last mean reversion parameters from db and initializing the
        # MarginController .If there are no parameters we use the current config.
        # In this case we set the control variable to k_m, because we don't want
        # to start with a margin of 0.
        # The parameters are updated again with the current config in the calling
        # part of the controller.
        mr_params = None
        if last_run_id:
            mr_params = self.pid_store.mr_params_by_run(last_run_id)

        if not mr_params:
            mr_params = config.margin.mr_controller

        self.margin_controller = MarginController(mr_params, self.last_timestamp)

        # Next step is to recreate the SpreadControllers for the peers of the last
        # run.
        # Controllers for new peers are created in the calling part.
        last_ewma_params = {}
        if last_run_id:
            last_ewma_params = self.pid_store.ewma_params_by_run(last_run_id)
        self.spread_controller_map: dict[str, SpreadController] = {}
        for pub_key, params in last_ewma_params.items():
            self.spread_controller_map[pub_key] = SpreadController(
                params, self.last_timestamp, pub_key
            )

        self.spread_level_controller: EwmaController | None = None

    def __call__(
        self, config: PidConfig, ln: LightningCache, timestamp_start: datetime
    ) -> PidResult:
        """
        Updates the MarginController and all SpreadControllers with new data
        fetched from the LN Node.
        """

        last_run_id, _ = self.pid_store.pid_run_last()

        peer_results: dict[str, PidPeerResult] = {}

        # Peers which require a force with the next policy update. Can be peers
        # with new channels or peers with a change of the reference fee rate.
        peers_update_force = set()

        # We need the last margin later we have to recalculate the spread for
        # one peer
        margin_last = self.margin_controller.margin

        # Calling the margin controller
        self.margin_controller(timestamp_start, config.margin.mr_controller)
        logger.debug(
            f"Called margin controller with args: timestamp {timestamp_start}; "
            f"params {config.margin.mr_controller}; result margin: "
            f"{self.margin_controller.margin:,.2f}"
        )

        # Setup of the channel aggregator. It needs the current block height to
        # determine new channels and the policies of the last run to determine
        # externally changes of the feerate.
        block_height = ln.lnclient.block_height
        if not last_run_id:
            last_policies = {}
        else:
            last_policies = self.ln_store.local_policies(last_run_id, 1)
            # last_ln_run = self.pid_store.last_ln_run()
            # last_policies = self.pid_store.last_policies_end(last_ln_run)

        aggregator = ChannelAggregator.from_channels(
            config=config,
            policies_last=last_policies,
            block_height=block_height,
            channels=ln.channels.values(),
        )

        # The default which is used if there is no target specified.
        target_default = aggregator.target_default
        logger.debug(f"Calculated {target_default=:,.2f}")

        # We want to remove unused spread controllers at the end. That's why
        # we store current pub keys.
        pub_keys_current = []
        for pub_key, channel_collection in aggregator.pid_collections():
            pub_keys_current.append(pub_key)

            spread_controller = self.spread_controller_map.get(pub_key)
            peer_config = config.peer_config(pub_key)

            # spread_recalibrated is set, if a recalibration of the spread was needed.
            spread_recalibrated: float | None = None

            def recalibrate_spread() -> None:
                # Recalculates the spread with the current reference fee rate.
                nonlocal spread_recalibrated

                # The spread s calibrated such that r_o = m_c + s * ( 1+ p/100 ),
                # with r_o is the ref_fee_rate, m_c the constant margin and p the
                # idiosyncratic margin in percent. Hence s = (r_o - m_c) / (1 + p/100).
                margin_const = margin_last + peer_config.margin_idiosyncratic

                spread_recalibrated = (
                    channel_collection.ref_fee_rate - margin_const
                ) / (1 + peer_config.margin_idiosyncratic_pct / 100)
                logger.debug(
                    f"Spread for {pub_key=} calibrated; "
                    f"calculated {spread_recalibrated=:,.2f}; "
                    f"{channel_collection.ref_fee_rate_last=}; "
                    f"{channel_collection.ref_fee_rate=}; {margin_last=:,.2f}; "
                    f"{peer_config.margin_idiosyncratic=:,.2f}; "
                    f"{peer_config.margin_idiosyncratic_pct=:,.2f}"
                )

            # If the reference fee rate of the channels has changed due to manual
            # interventions outside of the controller, we have to reset the control
            # variable. Otherwise the manual intervention will be overwritten by the
            # controller.
            if channel_collection.ref_fee_rate_changed:
                logger.debug(f"Reference fee rate changed for {pub_key=}")
                recalibrate_spread()

                # Make sure that the other channels have the same fee rate after
                # the run. If it not intended the user has to exclude these channels.
                peers_update_force.add(pub_key)

            # If there is no existing controller we have to create one
            if not spread_controller:
                # We check if there was a controller with this peer in the past,
                # we use this params at starting point for the control variable.
                timestamp, params = self.pid_store.ewma_params_last_by_peer(pub_key)

                logger.debug(
                    f"No existing spread controller for {pub_key=}; {timestamp=}; "
                    f"{params=}; {spread_recalibrated=}"
                )

                # Fallback to current config if there is no historic controller.
                if not params:
                    if spread_recalibrated is None:
                        # Can happen if the pub_key was on an exclude list when
                        # it was first seen. channel_collection.ref_fee_rate_changed
                        # was False in this case.
                        recalibrate_spread()
                    params = peer_config.ewma_controller

                # Fallback to current config if the historic controller is too old.
                if timestamp:
                    delta_hours = (timestamp_start - timestamp).total_seconds() / 3600
                    logger.debug(
                        f"Delta hours for {pub_key=}: {delta_hours:.2f}; "
                        f"{config.max_age_spread_hours=}"
                    )
                    if delta_hours > config.max_age_spread_hours:
                        if spread_recalibrated is None:
                            # Can also happen if channel was a longer time on the
                            # exclude list. channel_collection.ref_fee_rate_changed
                            # was False in this case.
                            recalibrate_spread()
                        params = peer_config.ewma_controller
                    else:
                        # if we use the historic controller, we do not want a
                        # recalibrated spread.
                        spread_recalibrated = None

                spread_controller = self.spread_controller_map[pub_key] = (
                    SpreadController(params, self.last_timestamp, pub_key)
                )

            # Now we have a spread controller for each peer and we can prepare
            # the call of the controller. We set the arguments for the call first.
            # If the alpha parameters have changed since the last run, we get a
            # ReinitRequired error. In this case we initialize a new controller
            # from the start with its whole history,
            target = peer_config.target or target_default

            def call_spread_controller(controller: SpreadController):
                controller(
                    timestamp_start,
                    channel_collection.liquidity_in,
                    channel_collection.liquidity_out,
                    peer_config.ewma_controller,
                    target,
                    peer_config.error_max,
                    peer_config.error_min,
                    peer_config.ratio_error_max,
                    peer_config.ratio_error_min,
                    spread_recalibrated,
                )
                logger.debug(
                    f"Called spread controller for {pub_key} with args: "
                    f"{timestamp_start=}; {peer_config=}; {target=:,.2f}; "
                    f"{controller.spread=:,.2f}"
                )

            try:
                call_spread_controller(spread_controller)
            except ReinitRequired:
                logger.info(f"Reinit required for {pub_key}")
                history = self.pid_store.ewma_params_by_pub_key(pub_key)
                spread_controller = self.spread_controller_map[pub_key] = (
                    SpreadController.from_history(
                        peer_config.ewma_controller, history, pub_key
                    )
                )
                call_spread_controller(spread_controller)

            # Force update to make sure that the new fee rate is broadcasted or
            # to align the fee rates of new channels with the existing ones.
            if channel_collection.has_new_channels:
                peers_update_force.add(pub_key)

            peer_results[pub_key] = peer_result = PidPeerResult(
                spread_controller=spread_controller,
                target=target,
                channel_collection=channel_collection,
                config=peer_config,
            )

            logger.debug(
                f"Created peer result for {pub_key=}: "
                f"{peer_result.margin_idiosyncratic=:,.2f}"
            )

        # If the channels with a peer has been closed, we can remove the controller
        # from the map. Therefore wie create a new map with the current pub keys.
        self.spread_controller_map = {
            k: v for k, v in self.spread_controller_map.items() if k in pub_keys_current
        }

        # Pre last step is the (optional) feature for a pinned peer. If a peer is
        # pinned, you can choose if you want to keep the fee rate or the spread
        # constant at the specified pin value for this peer.
        # Then the delta between the pin value and the current value is calculated.
        # This delta is applied as a shift to all spread controllers, which changes
        # the spreads of all controllers about the value.
        shift = 0
        if (pin_peer := config.pin_peer) is not None:

            # If the pin peer is set to "dynamic", we select the peer with the lowest
            # spread controller spread.
            if pin_peer == "dynamic" and len(peer_results) > 0:
                pin_peer = min(
                    peer_results, key=lambda p: peer_results[p].spread_controller.spread
                )
                logger.debug(f"Dynamic pin peer selected: {pin_peer=}")

            peer_result = peer_results.get(pin_peer)

            if peer_result is not None:
                if config.pin_method == "fee_rate":
                    shift = config.pin_value - (
                        self.margin_controller.margin
                        + peer_result.margin_idiosyncratic
                        + peer_result.spread_controller.spread
                    )
                elif config.pin_method == "spread":
                    shift = config.pin_value - peer_result.spread_controller.spread

        # Experimental feature of a spread level controller. It is a simple ewma
        # controller set up with k_p only.
        # It uses as error function the difference between the average spread rate
        # (remote liquidity weighted) and average spread rate (target weighted)
        # minus the specified target_ppm.
        # The difference is bounded by +/- max_deviation_ppm and normed
        # by 2 * max_deviation_ppm. Hence we receive an error in the range [-0.5, 0.5].
        # TODO: Setup a full ewma controller when we know this is the way to go.

        if (max_dev := config.spread_level_max_deviation_ppm) > 0:

            # sum of spread rate * target
            sum_target_weighted: float = 0
            # sum of target
            sum_target: float = 0
            # sum of spread rate * remote liquidity
            sum_remote_weighted: float = 0
            # sum of remote liquidity
            sum_remote: float = 0

            margin = self.margin_controller.margin

            for p in peer_results.values():
                if (col := p.channel_collection) is None:
                    continue

                # We floor the spread by the negative margin, to avoid the usage
                # of high negative spreads.
                spread = max(p.spread_controller.spread, -margin)

                liq_remote = col.liquidity_in
                liq_local = col.liquidity_out

                target_value = (liq_local + liq_remote) * p.target / PEER_TARGET_UNIT

                sum_target_weighted += spread * target_value
                sum_target += target_value
                sum_remote_weighted += spread * liq_remote
                sum_remote += liq_remote

            try:
                target_ppm = config.spread_level_target_ppm
                avg_spread_target = sum_target_weighted / sum_target
                avg_spread_remote = sum_remote_weighted / sum_remote
                spread_diff_bounded = spread_diff = (
                    avg_spread_remote - avg_spread_target - target_ppm
                )

                if spread_diff > max_dev:
                    spread_diff_bounded = max_dev
                elif spread_diff < -max_dev:
                    spread_diff_bounded = -max_dev

                error = spread_diff_bounded / (2 * max_dev)
                logger.debug(
                    f"Error calculated for spread level controller: "
                    f"{avg_spread_target=:.2f}, {avg_spread_remote=:.2f}, "
                    f"{target_ppm=:.2f}, {spread_diff=:.2f}, {max_dev=}, "
                    f"{spread_diff_bounded=:.2f}, {error=:.6f}"
                )
            except ZeroDivisionError:
                logger.error(
                    "ZeroDivisionError during calculation of spread "
                    "level controller error"
                )
                error = 0

            # If not set init a new EwmaController. Because it uses only a
            # proportional component we don't have to care about the starting
            # values for the ewmas.
            if self.spread_level_controller is None:
                self.spread_level_controller = EwmaController.from_params(
                    config.spread_level_params,
                    self.last_timestamp,
                )

                # Workaround: We set the error after init to the current error to
                # avoid a linear interpolation between 0 and the current after
                # each restart. We can remove it, when we store the historic
                # errors in the database.
                self.spread_level_controller.error = error

            # Maybe k_p has changed since the last run
            self.spread_level_controller.set_k_p(config.spread_level_params.k_p)

            # Call the controller
            self.spread_level_controller(error=error, timestamp=timestamp_start)
            shift = self.spread_level_controller.gain
            logger.debug(
                f"Called spread level controller: {timestamp_start=}, {error=:.6f}, "
                f"spread level controller: {config.spread_level_params}, {shift=:.6f}"
            )

        else:
            # Reset the spread level controller if not needed.
            self.spread_level_controller = None

        if shift != 0:
            logger.debug(f"Shifting spread controllers by {shift=:.6f}")
            for c in self.spread_controller_map.values():
                c.ewma_controller.apply_shift(shift)

        self.last_timestamp = timestamp_start

        return PidResult(
            config=config,
            peer_result_map=peer_results,
            margin_controller=self.margin_controller,
            peers_update_force=peers_update_force,
        )
