"""
Defines MarginController, SpreadController and the PidController
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Generator

from feelancer.lightning.chan_updates import PolicyProposal
from feelancer.lightning.data import LightningStore

from .aggregator import ChannelAggregator
from .analytics import EwmaController, MrController
from .data import (
    PidStore,
    new_margin_controller,
    new_pid_result,
    new_pid_run,
    new_spread_controller,
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
        pub_key: str = "",
    ):
        self.target = 0
        self._channel_collection: ChannelCollection | None = None
        self._pub_key = pub_key

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

        # Next step is the calculation of the error we need for the EwmaController.
        # The error is 0 if liquidity_in (normalized in millionths) is at the
        # target. If liquidity_in is higher than the target the error is in the
        # range ]0; 0.5]. And if liquidity_in is lower than the target the error
        # is in the range [-0.5; 0[.
        liquidity_out = channel_collection.liquidity_out
        liquidity_in = channel_collection.liquidity_in

        liquidity_total = liquidity_in + liquidity_out
        try:
            ratio_in = liquidity_in / liquidity_total
            set_point = target / PEER_TARGET_UNIT
            logging.debug(
                f"Set point calculated for {self._pub_key}; {ratio_in=}; "
                f"{set_point=}"
            )

            # Interpolate with piecewise linear functions between [-0.5; 0.5]
            if ratio_in >= set_point:
                error = 0.5 / (1 - set_point) * (ratio_in - set_point)
            else:
                error = 0.5 / set_point * (ratio_in - set_point)

            logging.debug(f"Error calculated for {self._pub_key}; {error=}")
        except ZeroDivisionError:
            error = 0
            logging.error(f"ZeroDivisionError for {self._pub_key}; {error=}")

        # If the reference fee rate of the channels has changed due to manual
        # interventions outside of the controller, we have to reset the control
        # variable. Otherwise the manual intervention will be overwritten by the
        # controller.
        if channel_collection.ref_fee_rate_changed:
            spread_new = channel_collection.ref_fee_rate - margin
            logging.debug(
                f"Reference fee rate changed for {self._pub_key}; "
                f"set control variable to {spread_new=}; "
                f"{channel_collection.ref_fee_rate_last=}; "
                f"{channel_collection.ref_fee_rate=}; {margin=}"
            )
            self.ewma_controller.control_variable = spread_new

        # Now we are able to call the actual ewma controller
        self.ewma_controller(error, timestamp)

        self.target = target
        self._channel_collection = channel_collection

    def channels(self) -> Generator[Channel, None, None]:
        """
        Yields all channels associated with this controller.
        """
        if not self._channel_collection:
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
        pub_key: str = "",
    ):
        """
        Initializes a new controller with the provided ewma params and calls
        it for the whole history.
        """
        if len(history) == 0:
            return cls(ewma_params, None, pub_key)

        timestamp_init = history[0][0] - timedelta(seconds=history[0][2])
        controller = cls(ewma_params, timestamp_init, pub_key)

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


def new_policy_proposal(pid_result: PidResult, set_inbound: bool) -> PolicyProposal:
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
    """
    The main controller which holds the MarginController and all SpreadControllers.
    """

    def __init__(self, db: FeelancerDB, config: PidConfig, pubkey_local: str) -> None:
        self.config = config
        self.pid_store = PidStore(db, pubkey_local)
        self.ln_store = LightningStore(db, pubkey_local)
        self.pubkey_local = pubkey_local

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
            mr_params = self.config.margin.mr_controller

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

    def __call__(
        self, config: PidConfig, ln: LightningCache, timestamp_start: datetime
    ) -> None:
        """
        Updates the MarginController and all SpreadControllers with new data
        fetched from the LN Node.
        """

        last_run_id, _ = self.pid_store.pid_run_last()
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
            config=self.config,
            policies_last=last_policies,
            block_height=block_height,
            channels=ln.channels.values(),
        )

        # The default which is used if there is no target specified.
        target_default = aggregator.target_default
        logging.debug(f"{target_default=}")

        # We want to remove unused spread controllers at the end. That's why
        # we store current pub keys.
        pub_keys_current = []
        for pub_key, channel_collection in aggregator.pid_collections():
            pub_keys_current.append(pub_key)

            spread_controller = self.spread_controller_map.get(pub_key)
            peer_config = self.config.peer_config(pub_key)

            # If there is no existing controller we have to create one
            if not spread_controller:
                # We check if there was a controller with this peer in the past,
                # we use this params at starting point for the control variable.
                timestamp, params = self.pid_store.ewma_params_last_by_peer(pub_key)

                # Fallback to current config if there is no historic controller.
                if not params:
                    params = peer_config.ewma_controller

                # Fallback to current config if the historic controller is too old.
                if timestamp:
                    delta_hours = (timestamp_start - timestamp).total_seconds() / 3600
                    if delta_hours > config.max_age_spread_hours:
                        params = peer_config.ewma_controller

                spread_controller = self.spread_controller_map[pub_key] = (
                    SpreadController(params, self.last_timestamp, pub_key)
                )

            # Now we have a spread controller for each peer and we can prepare
            # the call of the controller. We set the arguments for the call first.
            # If the alpha parameters have changed since the last run, we get a
            # ReinitRequired error. In this case we initialize a new controller
            # from the start with its whole history,
            target = peer_config.target or target_default
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
                history = self.pid_store.ewma_params_by_pub_key(pub_key)
                spread_controller = self.spread_controller_map[pub_key] = (
                    SpreadController.from_history(
                        peer_config.ewma_controller, history, pub_key
                    )
                )
                spread_controller(*call_args)

            logging.debug(
                f"Called spread controller for {pub_key} with args: "
                f"timestamp {timestamp_start}; params {peer_config.ewma_controller}; "
                f"target {target}; margin peer {margin_peer}; result spread: "
                f"{spread_controller.spread}"
            )

        # If the channels with a peer has been closed, we can remove the controller
        # from the map. Therefore wie create a new map with the current pub keys.
        self.spread_controller_map = {
            k: v for k, v in self.spread_controller_map.items() if k in pub_keys_current
        }

        # Last step is the (optional) feature for a pinned peer. If a peer is
        # pinned, you can choose if you want to keep the fee rate or the spread
        # constant at the specified pin value for this peer.
        # Then the delta between the pin value and the current value is calculated.
        # This delta is applied as a shift to all spread controllers, which changes
        # the spreads of all controllers about the value.
        if (pin_peer := config.pin_peer) is not None:
            pin_controller = self.spread_controller_map.get(pin_peer)

            if pin_controller is not None:
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
        """
        Generates all sqlalchemy objects with the results of the last call of
        the PidController.
        """

        pid_run = new_pid_run(ln_session.db_run, ln_session.ln_node)
        yield new_margin_controller(pid_run, self.margin_controller)

        for pub_key, spread_controller in self.spread_controller_map.items():
            peer = ln_session.channel_peer_by(pub_key=pub_key)

            yield new_spread_controller(spread_controller, peer, pid_run)

            peer_config = self.config.peer_config(pub_key)
            margin_idio = peer_config.margin_idiosyncratic
            for res in yield_pid_results(
                self.margin_controller, spread_controller, margin_idio
            ):
                channel = ln_session.channel_static_by(channel=res.channel)
                yield new_pid_result(res, channel, pid_run)

    def policy_proposals(self) -> list[PolicyProposal]:
        """
        Creates a list of all PolicyProposals with the results of the last call of
        the PidController.
        """

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
                res.append(new_policy_proposal(r, set_inbound))

        return res
