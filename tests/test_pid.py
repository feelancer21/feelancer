from __future__ import annotations

import copy
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import MagicMock, patch

from test_analytics import EwmaCall, call_ewma

from feelancer.lightning.chan_updates import PolicyProposal
from feelancer.lightning.client import Channel, ChannelPolicy, LightningClient
from feelancer.lightning.data import LightningCache
from feelancer.pid.aggregator import ChannelAggregator, ChannelCollection
from feelancer.pid.analytics import EwmaControllerParams, MrControllerParams
from feelancer.pid.controller import PidController, SpreadController, _calc_error
from feelancer.pid.data import (
    PidConfig,
    PidMarginControllerConfig,
    PidSpreadControllerConfig,
)


def _new_mock_channel_policy(fee_rate_ppm: int) -> ChannelPolicy:
    p = cast(ChannelPolicy, MagicMock())
    p.fee_rate_ppm = fee_rate_ppm
    return p


def _new_mock_channel(
    pub_key: str,
    chan_id: int,
    chan_point: str,
    capacity: int,
    private: bool,
    opening_height: int,
    liq_out: int,
    liq_in: int,
    liq_out_pen: int,
    liq_in_pen: int,
    fee_rate_ppm: int,
) -> Channel:
    c = cast(Channel, MagicMock())
    c.pub_key = pub_key
    c.chan_id = chan_id
    c.chan_point = chan_point
    c.capacity_sat = capacity
    c.private = private
    c.opening_height = opening_height
    c.liquidity_in_settled_sat = liq_in
    c.liquidity_out_settled_sat = liq_out
    c.liquidity_in_pending_sat = liq_in_pen
    c.liquidity_out_pending_sat = liq_out_pen
    c.policy_local = _new_mock_channel_policy(fee_rate_ppm)
    return c


def _new_mock_margin_config(k_m: float, alpha: float) -> PidMarginControllerConfig:
    c = cast(PidMarginControllerConfig, MagicMock())
    c.mr_controller = MrControllerParams(k_m=k_m, alpha=alpha)

    return c


def _new_mock_pid_config(
    exclude_pubkeys: list[str],
    exclude_chanid: list[int],
    max_age_new_channels: int,
    config: dict[str, PidSpreadControllerConfig],
    default_config: PidSpreadControllerConfig,
    margin_config: PidMarginControllerConfig,
) -> PidConfig:

    c = cast(PidConfig, MagicMock())
    c.exclude_pubkeys = exclude_pubkeys
    c.exclude_chanids = exclude_chanid
    c.max_age_new_channels = max_age_new_channels
    c.margin = margin_config
    c.db_only = False
    c.spread_level_max_deviation_ppm = 0
    c.spread_level_target_ppm = 0

    # peer_config returns the value of dict config, and uses default_config
    # as default.
    def peer_config(pub_key: str) -> PidSpreadControllerConfig:
        return config.get(pub_key, default_config)

    c.peer_config = peer_config
    return c


def _new_mock_lnclient(block_height: int, channels: list[Channel]) -> LightningClient:
    c = MagicMock(spec=LightningClient)
    c.channels = {ch.chan_id: ch for ch in channels}
    c.block_height = block_height
    return c


class NoExpectedResult: ...
"""
    A class if we don't expect any result to have a separation from results which
    are None. An instance can be used if we do not want to generate a target result
    for an aspect of a test case, e.g. if this aspect has already been tested
    sufficiently in previous test cases.
"""

# An instance of NoExpectedResult
NO_RES = NoExpectedResult()


@dataclass
class ERPidChannelCollection:
    """Expected result for a channel collection."""

    # The next attribute are the expected results of the associated with attributes
    # or methods of the ChannelCollection
    liquidity_in: float
    liquidity_out: float
    private_only: bool
    ref_fee_rate: int
    ref_fee_rate_last: int | None
    ref_fee_rate_changed: bool

    # Expected chan_ids of the pid_channels method.
    chan_ids: list[int]

    has_new_channels: bool


@dataclass
class ERPidAggregator:
    """Expected result for a aggregator."""

    target_default: float | NoExpectedResult
    channel_collection: dict[str, ERPidChannelCollection] | NoExpectedResult


@dataclass
class TCasePidAggregator:
    """
    Testcase for a pid aggregator.
    """

    # name of the testcase
    name: str

    # description of the testcase
    description: str

    config: PidConfig
    # dict of the final policies of the last run
    policies_last: dict[int, ChannelPolicy]
    # current block height
    block_height: int
    # channels which are used to initialize this aggregator
    channels: list[Channel]

    expected_result: ERPidAggregator


@dataclass
class ERSpreadRateController:
    """
    Expected result of one call of a spread reate controller.
    """

    spread: float
    target: float


@dataclass
class ERPidControllerCall:
    """
    Expected result for one call of a pid controller.
    """

    margin_rate: float
    spread_controller_results: dict[str, ERSpreadRateController]
    policy_proposals: list[PolicyProposal]


@dataclass
class PidControllerCall:
    """
    Data for one call of a pid controller-
    """

    timestamp: datetime

    config: PidConfig

    # return value of pid_store.pid_run_last()
    pid_run_last: tuple[int, datetime] | tuple[None, None]
    # dict which stores per peer the result of pid_store.ewma_params_last_by_peer
    ewma_params_last: dict[str, tuple[datetime, EwmaControllerParams]]
    # dict of the final policies of the last run
    policies_last: dict[int, ChannelPolicy]
    # current block height
    block_height: int
    # channels which are used to initialize this aggregator
    channels: list[Channel]

    expected_result: ERPidControllerCall | NoExpectedResult


@dataclass
class TCasePidController:
    """
    Testcase for a pid controller.
    """

    # name of the testcase
    name: str

    # description of the testcase
    description: str

    calls: list[PidControllerCall]


@dataclass
class TCasePidError:
    # name of the testcase
    name: str

    # description of the testcase
    description: str

    liquidity_in: float
    liquidity_out: float
    target: float

    expected_error: float


class TestPid(unittest.TestCase):

    def setUp(self):
        self.testcases_aggregator: list[TCasePidAggregator] = []
        self.testcases_controller: list[TCasePidController] = []

        # base time for controller calls
        time_base = datetime(2021, 1, 1, 0, 0, 0)

        margin_1 = _new_mock_margin_config(40, 0.02)

        configs_1: dict[str, PidSpreadControllerConfig] = {}
        exclude_pubkeys_1: list[str] = []
        exclude_chanid_1: list[int] = []

        exclude_pubkeys_2: list[str] = ["carol"]
        exclude_chanid_2: list[int] = [2]

        max_age_new_channels = 1000

        default_ewma = EwmaControllerParams(
            k_p=120,
            k_i=480,
            k_d=240,
            alpha_d=1.0 * 24,
            alpha_i=0.04 * 24,
        )

        default_config = PidSpreadControllerConfig(
            fee_rate_new_local=1000,
            fee_rate_new_remote=210,
            target=None,
            ewma_controller=default_ewma,
        )

        bob_ewma_2 = EwmaControllerParams(
            k_p=240,
            k_i=960,
            k_d=480,
            alpha_d=1.0 * 24,
            alpha_i=0.04 * 24,
            control_variable=210,
        )

        bob_ewma_3 = EwmaControllerParams(
            k_p=200,
            k_i=300,
            k_d=400,
            alpha_d=1.0 * 24,
            alpha_i=0.04 * 24,
            control_variable=210,
        )

        configs_2 = {
            "bob": PidSpreadControllerConfig(
                fee_rate_new_local=2000,
                fee_rate_new_remote=420,
                target=400_000,
                ewma_controller=bob_ewma_2,
            )
        }

        configs_3 = {
            "bob": PidSpreadControllerConfig(
                fee_rate_new_local=2000,
                fee_rate_new_remote=420,
                target=400_000,
                ewma_controller=bob_ewma_3,
            )
        }

        # config is an empty dict.
        pid_config = _new_mock_pid_config(
            exclude_pubkeys=exclude_pubkeys_1,
            exclude_chanid=exclude_chanid_1,
            max_age_new_channels=max_age_new_channels,
            config=configs_1,
            default_config=default_config,
            margin_config=margin_1,
        )

        # config has SpreadControllerConfig for bob.
        pid_config_2 = _new_mock_pid_config(
            exclude_pubkeys=exclude_pubkeys_1,
            exclude_chanid=exclude_chanid_1,
            max_age_new_channels=max_age_new_channels,
            config=configs_2,
            default_config=default_config,
            margin_config=margin_1,
        )

        # config with other exclude.
        pid_config_3 = _new_mock_pid_config(
            exclude_pubkeys=exclude_pubkeys_2,
            exclude_chanid=exclude_chanid_2,
            max_age_new_channels=max_age_new_channels,
            config=configs_1,
            default_config=default_config,
            margin_config=margin_1,
        )

        # Config with changed ewma parameters.
        pid_config_4 = _new_mock_pid_config(
            exclude_pubkeys=exclude_pubkeys_1,
            exclude_chanid=exclude_chanid_1,
            max_age_new_channels=max_age_new_channels,
            config=configs_3,
            default_config=default_config,
            margin_config=margin_1,
        )

        bob_chan_1 = _new_mock_channel(
            pub_key="bob",
            chan_id=1,
            chan_point="bob_1",
            capacity=4_000_000,
            private=False,
            opening_height=750_000,
            liq_out=750_000,
            liq_in=1_500_000,
            liq_out_pen=250_000,
            liq_in_pen=1_500_000,
            fee_rate_ppm=100,
        )

        # Same but opened recently, balance remote
        bob_chan_2 = _new_mock_channel(
            pub_key="bob",
            chan_id=2,
            chan_point="bob_2",
            capacity=4_000_000,
            private=False,
            opening_height=839_000,
            liq_out=750_000,
            liq_in=1_500_000,
            liq_out_pen=250_000,
            liq_in_pen=1_500_000,
            fee_rate_ppm=100,
        )

        # Same but opened recently, balance remote
        bob_chan_3 = _new_mock_channel(
            pub_key="bob",
            chan_id=3,
            chan_point="bob_3",
            capacity=4_000_000,
            private=False,
            opening_height=839_000,
            liq_out=2_000_000,
            liq_in=0,
            liq_out_pen=0,
            liq_in_pen=0,
            fee_rate_ppm=100,
        )

        # Same but opened recently, balance remote, max_age_new_channels passed
        bob_chan_4 = _new_mock_channel(
            pub_key="bob",
            chan_id=4,
            chan_point="bob_4",
            capacity=4_000_000,
            private=False,
            opening_height=838_999,
            liq_out=2_000_000,
            liq_in=0,
            liq_out_pen=0,
            liq_in_pen=0,
            fee_rate_ppm=100,
        )

        # channel 1 but bob raised the fee now
        bob_chan_1_up = _new_mock_channel(
            pub_key="bob",
            chan_id=1,
            chan_point="bob_1",
            capacity=4_000_000,
            private=False,
            opening_height=750_000,
            liq_out=750_000,
            liq_in=1_500_000,
            liq_out_pen=250_000,
            liq_in_pen=1_500_000,
            fee_rate_ppm=200,
        )

        # channel 1 but bob lowered the fee now to 0
        bob_chan_1_down = _new_mock_channel(
            pub_key="bob",
            chan_id=1,
            chan_point="bob_1",
            capacity=4_000_000,
            private=False,
            opening_height=750_000,
            liq_out=750_000,
            liq_in=1_500_000,
            liq_out_pen=250_000,
            liq_in_pen=1_500_000,
            fee_rate_ppm=0,
        )

        # like bob2 but with raised fee rate
        bob_chan_2_up = _new_mock_channel(
            pub_key="bob",
            chan_id=2,
            chan_point="bob_2",
            capacity=4_000_000,
            private=False,
            opening_height=839_000,
            liq_out=750_000,
            liq_in=1_500_000,
            liq_out_pen=250_000,
            liq_in_pen=1_500_000,
            fee_rate_ppm=200,
        )

        # like bob2 but with lowered fee rate
        bob_chan_2_down = _new_mock_channel(
            pub_key="bob",
            chan_id=2,
            chan_point="bob_2",
            capacity=4_000_000,
            private=False,
            opening_height=839_000,
            liq_out=750_000,
            liq_in=1_500_000,
            liq_out_pen=250_000,
            liq_in_pen=1_500_000,
            fee_rate_ppm=0,
        )

        # like bob3 but with raised fee rate
        bob_chan_3_up = _new_mock_channel(
            pub_key="bob",
            chan_id=3,
            chan_point="bob_3",
            capacity=4_000_000,
            private=False,
            opening_height=839_000,
            liq_out=2_000_000,
            liq_in=0,
            liq_out_pen=0,
            liq_in_pen=0,
            fee_rate_ppm=200,
        )

        # like bob2 but with lowered fee rate
        bob_chan_3_down = _new_mock_channel(
            pub_key="bob",
            chan_id=3,
            chan_point="bob_3",
            capacity=4_000_000,
            private=False,
            opening_height=839_000,
            liq_out=2_000_000,
            liq_in=0,
            liq_out_pen=0,
            liq_in_pen=0,
            fee_rate_ppm=0,
        )

        # like bob3, but no policy
        bob_chan_5 = _new_mock_channel(
            pub_key="bob",
            chan_id=5,
            chan_point="bob_5",
            capacity=4_000_000,
            private=False,
            opening_height=839_000,
            liq_out=2_000_000,
            liq_in=0,
            liq_out_pen=0,
            liq_in_pen=0,
            fee_rate_ppm=0,
        )
        bob_chan_5.policy_local = None

        # like bob3, but no policy and private
        bob_chan_6 = _new_mock_channel(
            pub_key="bob",
            chan_id=6,
            chan_point="bob_6",
            capacity=4_000_000,
            private=True,
            opening_height=839_000,
            liq_out=2_000_000,
            liq_in=0,
            liq_out_pen=0,
            liq_in_pen=0,
            fee_rate_ppm=0,
        )
        bob_chan_6.policy_local = None

        # The channel with carol is private
        carol_chan_1 = _new_mock_channel(
            pub_key="carol",
            chan_id=11,
            chan_point="carol_1",
            capacity=10_000_000,
            private=True,
            opening_height=700_000,
            liq_out=2_000_000,
            liq_in=0,
            liq_out_pen=0,
            liq_in_pen=0,
            fee_rate_ppm=400,
        )

        # Another private channel with carol
        carol_chan_2 = _new_mock_channel(
            pub_key="carol",
            chan_id=12,
            chan_point="carol_2",
            capacity=10_000_000,
            private=True,
            opening_height=700_000,
            liq_out=1_000_000,
            liq_in=2_000_000,
            liq_out_pen=0,
            liq_in_pen=0,
            fee_rate_ppm=400,
        )

        # A public channel with carol
        carol_chan_3 = _new_mock_channel(
            pub_key="carol",
            chan_id=13,
            chan_point="carol_3",
            capacity=10_000_000,
            private=False,
            opening_height=700_000,
            liq_out=2_500_000,
            liq_in=0,
            liq_out_pen=0,
            liq_in_pen=3_000_000,
            fee_rate_ppm=400,
        )

        policy_bob_last_1 = _new_mock_channel_policy(fee_rate_ppm=100)
        policy_bob_last_2 = _new_mock_channel_policy(fee_rate_ppm=0)
        policy_carol_last_1 = _new_mock_channel_policy(fee_rate_ppm=400)

        ##################################################
        ##### Aggregator: Testcases with one channel #####
        ##################################################

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="1",
                description="single channel; public; not new; fee rate same",
                config=pid_config,
                policies_last={1: policy_bob_last_1},
                block_height=840_000,
                channels=[bob_chan_1],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=750000,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=1_000_000,
                            liquidity_in=3_000_000,
                            private_only=False,
                            ref_fee_rate=100,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=False,
                            chan_ids=[1],
                            has_new_channels=False,
                        )
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="2",
                description="single channel; public; new channel; liq_in>liq_out",
                config=pid_config,
                policies_last={},  # No policies because channel is new
                block_height=840_000,
                channels=[bob_chan_2],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=750000,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=1_000_000,
                            liquidity_in=3_000_000,
                            private_only=False,
                            # expect new remote fee, because liquidity in is higher
                            ref_fee_rate=210,
                            ref_fee_rate_last=None,
                            ref_fee_rate_changed=True,
                            chan_ids=[2],
                            has_new_channels=True,
                        )
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="3",
                description="single channel; public; new channel; liq_in<liq_out",
                config=pid_config,
                policies_last={},  # No policies because channel is new
                block_height=840_000,
                channels=[bob_chan_3],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=0,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=2_000_000,
                            liquidity_in=0,
                            private_only=False,
                            # expect new remote fee, because liquidity out is higher
                            ref_fee_rate=1000,
                            ref_fee_rate_last=None,
                            ref_fee_rate_changed=True,
                            chan_ids=[3],
                            has_new_channels=True,
                        )
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="4",
                description="single channel; public; new channel; max_age_new_channels passed; liq_in<liq_out",
                config=pid_config,
                policies_last={},  # No policies because channel is new
                block_height=840_000,
                channels=[bob_chan_4],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=0,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=2_000_000,
                            liquidity_in=0,
                            private_only=False,
                            # expect current fee rate because max_age_new_channels passed
                            ref_fee_rate=100,
                            ref_fee_rate_last=None,
                            ref_fee_rate_changed=True,
                            chan_ids=[4],
                            has_new_channels=False,
                        )
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="5",
                description="single channel; private; not new, fee rate same",
                config=pid_config,
                policies_last={11: policy_carol_last_1},
                block_height=840_000,
                channels=[carol_chan_1],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=500_000,  # returns the default
                    channel_collection={
                        "carol": ERPidChannelCollection(
                            liquidity_out=0,  # 0 because it is private only
                            liquidity_in=0,
                            private_only=True,
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[],  # empty because of private only
                            has_new_channels=False,
                        )
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="6",
                description="single channel; public; not new; bob raised fee",
                config=pid_config,
                policies_last={1: policy_bob_last_1},
                block_height=840_000,
                channels=[bob_chan_1_up],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=750000,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=1_000_000,
                            liquidity_in=3_000_000,
                            private_only=False,
                            ref_fee_rate=200,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=True,
                            chan_ids=[1],
                            has_new_channels=False,
                        )
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="7",
                description="single channel; public; not new; bob raised fee, last policy with fee_rate 0",
                config=pid_config,
                policies_last={1: policy_bob_last_2},
                block_height=840_000,
                channels=[bob_chan_1_up],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=750000,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=1_000_000,
                            liquidity_in=3_000_000,
                            private_only=False,
                            ref_fee_rate=200,
                            ref_fee_rate_last=0,
                            ref_fee_rate_changed=True,
                            chan_ids=[1],
                            has_new_channels=False,
                        )
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="8",
                description="single channel; public; not new; bob lowered fee",
                config=pid_config,
                policies_last={1: policy_bob_last_1},
                block_height=840_000,
                channels=[bob_chan_1_down],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=750000,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=1_000_000,
                            liquidity_in=3_000_000,
                            private_only=False,
                            ref_fee_rate=0,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=True,
                            chan_ids=[1],
                            has_new_channels=False,
                        )
                    },
                ),
            )
        )

        ########################################################
        ##### Aggregator: Testcases with multiple channels #####
        ########################################################

        # One Channel Party

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="9",
                description="two channels; both private; not new, fee rate same",
                config=pid_config,
                policies_last={11: policy_carol_last_1, 12: policy_carol_last_1},
                block_height=840_000,
                channels=[carol_chan_1, carol_chan_2],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=500_000,  # returns the default
                    channel_collection={
                        "carol": ERPidChannelCollection(
                            liquidity_out=0,  # 0 because it is private only
                            liquidity_in=0,
                            private_only=True,
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[],  # empty because of private only
                            has_new_channels=False,
                        )
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="10",
                description="three channels; two private and one public; not new, fee rate same",
                config=pid_config,
                policies_last={
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                    13: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[carol_chan_1, carol_chan_2, carol_chan_3],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=5_000_000 / 10.5,  # 5M / (5M + 5.5M) * 1e6
                    channel_collection={
                        "carol": ERPidChannelCollection(
                            liquidity_out=5_500_000,  # 2M + 1M + 2.5M = 5.5M
                            liquidity_in=5_000_000,  # 0 + 2M + 3M = 5M
                            private_only=False,  # One channel is public
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[
                                11,
                                12,
                                13,
                            ],  # not empty because of one public channel
                            has_new_channels=False,
                        )
                    },
                ),
            )
        )

        # Multiple Channel Parties

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="11",
                description="carol with two private, bob with three public",
                config=pid_config,
                policies_last={
                    1: policy_bob_last_1,
                    2: policy_bob_last_1,
                    3: policy_bob_last_1,
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[
                    bob_chan_1,
                    bob_chan_2,
                    bob_chan_3,
                    carol_chan_1,
                    carol_chan_2,
                ],
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=600_000,  # 6M / 10M * 1e6
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=4_000_000,  # 1M + 1M + 2M
                            liquidity_in=6_000_000,  # 3M +3M
                            private_only=False,
                            ref_fee_rate=100,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=False,
                            chan_ids=[1, 2, 3],
                            has_new_channels=False,
                        ),
                        "carol": ERPidChannelCollection(
                            liquidity_out=0,  # 0 because it is private only
                            liquidity_in=0,
                            private_only=True,
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[],  # empty because of private only
                            has_new_channels=False,
                        ),
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="12",
                description="carol with two private and one public channel, bob with three public",
                config=pid_config,
                policies_last={
                    1: policy_bob_last_1,
                    2: policy_bob_last_1,
                    3: policy_bob_last_1,
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                    13: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[
                    carol_chan_3,
                    bob_chan_1,
                    bob_chan_3,
                    carol_chan_2,
                    bob_chan_2,
                    carol_chan_1,
                ],  # Mixing the order
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=11_000_000 / 20.5,  # 11M / 20.5M * 1e6
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=4_000_000,  # 1M + 1M + 2M
                            liquidity_in=6_000_000,  # 3M +3M
                            private_only=False,
                            ref_fee_rate=100,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=False,
                            chan_ids=[1, 2, 3],
                            has_new_channels=False,
                        ),
                        "carol": ERPidChannelCollection(
                            liquidity_out=5_500_000,  # 2M + 1M + 2.5M = 5.5M
                            liquidity_in=5_000_000,  # 0 + 2M + 3M = 5M
                            private_only=False,  # One channel is public
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[11, 12, 13],
                            has_new_channels=False,
                        ),
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="13",
                description="carol with two private and one public channel, bob with three public, bob with individual config/target",
                config=pid_config_2,
                policies_last={
                    1: policy_bob_last_1,
                    2: policy_bob_last_1,
                    3: policy_bob_last_1,
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                    13: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[
                    carol_chan_3,
                    bob_chan_1,
                    bob_chan_3,
                    carol_chan_2,
                    bob_chan_2,
                    carol_chan_1,
                ],  # Mixing the order
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    # 10M * 400_000 + 10.5M * X = 11M
                    target_default=(11_000_000 - 4_000_000) / 10.5,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=4_000_000,  # 1M + 1M + 2M
                            liquidity_in=6_000_000,  # 3M +3M
                            private_only=False,
                            ref_fee_rate=100,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=False,
                            chan_ids=[1, 2, 3],
                            has_new_channels=False,
                        ),
                        "carol": ERPidChannelCollection(
                            liquidity_out=5_500_000,  # 2M + 1M + 2.5M = 5.5M
                            liquidity_in=5_000_000,  # 0 + 2M + 3M = 5M
                            private_only=False,  # One channel is public
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[11, 12, 13],
                            has_new_channels=False,
                        ),
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="14",
                description="carol with two private and one public channel, bob with three public, channel 2 and 3 new, bob with individual config/target",
                config=pid_config_2,
                policies_last={
                    1: policy_bob_last_1,  # deleted channel 2 and 3
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                    13: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[
                    carol_chan_3,
                    bob_chan_1,
                    bob_chan_3,
                    carol_chan_2,
                    bob_chan_2,
                    carol_chan_1,
                ],  # Mixing the order
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    # 10M * 400_000 + 10.5M * X = 11M
                    target_default=(11_000_000 - 4_000_000) / 10.5,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=4_000_000,  # 1M + 1M + 2M
                            liquidity_in=6_000_000,  # 3M +3M
                            private_only=False,
                            ref_fee_rate=100,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=False,
                            chan_ids=[1, 2, 3],
                            has_new_channels=True,
                        ),
                        "carol": ERPidChannelCollection(
                            liquidity_out=5_500_000,  # 2M + 1M + 2.5M = 5.5M
                            liquidity_in=5_000_000,  # 0 + 2M + 3M = 5M
                            private_only=False,  # One channel is public
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[11, 12, 13],
                            has_new_channels=False,
                        ),
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="15",
                description="carol with two private and one public channel, bob with three public, bob raised the fees, bob with individual config/target",
                config=pid_config_2,
                policies_last={
                    1: policy_bob_last_1,  # deleted channel 2 and 3
                    2: policy_bob_last_1,
                    3: policy_bob_last_1,
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                    13: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[
                    carol_chan_3,
                    bob_chan_1_up,
                    bob_chan_3_up,
                    carol_chan_2,
                    bob_chan_2_up,
                    carol_chan_1,
                ],  # Mixing the order
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    # 10M * 400_000 + 10.5M * X = 11M
                    target_default=(11_000_000 - 4_000_000) / 10.5,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=4_000_000,  # 1M + 1M + 2M
                            liquidity_in=6_000_000,  # 3M +3M
                            private_only=False,
                            ref_fee_rate=200,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=True,
                            chan_ids=[1, 2, 3],
                            has_new_channels=False,
                        ),
                        "carol": ERPidChannelCollection(
                            liquidity_out=5_500_000,  # 2M + 1M + 2.5M = 5.5M
                            liquidity_in=5_000_000,  # 0 + 2M + 3M = 5M
                            private_only=False,  # One channel is public
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[11, 12, 13],
                            has_new_channels=False,
                        ),
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="16",
                description="carol with two private and one public channel, bob with three public, bob lowered the fees, bob with individual config/target",
                config=pid_config_2,
                policies_last={
                    1: policy_bob_last_1,  # deleted channel 2 and 3
                    2: policy_bob_last_1,
                    3: policy_bob_last_1,
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                    13: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[
                    carol_chan_3,
                    bob_chan_1_down,
                    bob_chan_3_down,
                    carol_chan_2,
                    bob_chan_2_down,
                    carol_chan_1,
                ],  # Mixing the order
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    # 10M * 400_000 + 10.5M * X = 11M
                    target_default=(11_000_000 - 4_000_000) / 10.5,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=4_000_000,  # 1M + 1M + 2M
                            liquidity_in=6_000_000,  # 3M +3M
                            private_only=False,
                            ref_fee_rate=0,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=True,
                            chan_ids=[1, 2, 3],
                            has_new_channels=False,
                        ),
                        "carol": ERPidChannelCollection(
                            liquidity_out=5_500_000,  # 2M + 1M + 2.5M = 5.5M
                            liquidity_in=5_000_000,  # 0 + 2M + 3M = 5M
                            private_only=False,  # One channel is public
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[11, 12, 13],
                            has_new_channels=False,
                        ),
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="17",
                description="exclude 'carol' and channel 2 of bob per config",
                config=pid_config_3,
                policies_last={
                    1: policy_bob_last_1,
                    2: policy_bob_last_1,
                    3: policy_bob_last_1,
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                    13: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[
                    carol_chan_3,
                    bob_chan_1,
                    bob_chan_3,
                    carol_chan_2,
                    bob_chan_2,
                    carol_chan_1,
                ],  # Mixing the order
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=500_000,
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            liquidity_out=3_000_000,  # 1M + 2M
                            liquidity_in=3_000_000,  # 3M +3M
                            private_only=False,
                            ref_fee_rate=100,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=False,
                            chan_ids=[1, 3],
                            has_new_channels=False,
                        )
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="18",
                description="carol with two private and one public channel, bob with three public, one channel has no policy",
                config=pid_config,
                policies_last={
                    1: policy_bob_last_1,
                    2: policy_bob_last_1,
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                    13: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[
                    carol_chan_3,
                    bob_chan_1,
                    bob_chan_5,  # Channel has no policy
                    carol_chan_2,
                    bob_chan_2,
                    carol_chan_1,
                ],  # Mixing the order
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=11_000_000 / 18.5,  # 11M / 18.5M * 1e6
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            # channel 5 is skipped because of missing policy
                            liquidity_out=2_000_000,  # 1M + 1M
                            liquidity_in=6_000_000,  # 3M +3M
                            private_only=False,
                            ref_fee_rate=100,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=False,
                            chan_ids=[1, 2],
                            has_new_channels=False,
                        ),
                        "carol": ERPidChannelCollection(
                            liquidity_out=5_500_000,  # 2M + 1M + 2.5M = 5.5M
                            liquidity_in=5_000_000,  # 0 + 2M + 3M = 5M
                            private_only=False,  # One channel is public
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[11, 12, 13],
                            has_new_channels=False,
                        ),
                    },
                ),
            )
        )

        self.testcases_aggregator.append(
            TCasePidAggregator(
                name="19",
                description="carol with two private and one public channel, bob with two public, one private, private channel has no policy",
                config=pid_config,
                policies_last={
                    1: policy_bob_last_1,
                    2: policy_bob_last_1,
                    11: policy_carol_last_1,
                    12: policy_carol_last_1,
                    13: policy_carol_last_1,
                },
                block_height=840_000,
                channels=[
                    carol_chan_3,
                    bob_chan_1,
                    bob_chan_6,  # Channel has no policy
                    carol_chan_2,
                    bob_chan_2,
                    carol_chan_1,
                ],  # Mixing the order
                #
                # Expected result here
                expected_result=ERPidAggregator(
                    target_default=11_000_000 / 18.5,  # 11M / 18.5M * 1e6
                    channel_collection={
                        "bob": ERPidChannelCollection(
                            # channel 5 is skipped because of missing policy
                            liquidity_out=2_000_000,  # 1M + 1M
                            liquidity_in=6_000_000,  # 3M +3M
                            private_only=False,
                            ref_fee_rate=100,
                            ref_fee_rate_last=100,
                            ref_fee_rate_changed=False,
                            chan_ids=[1, 2],
                            has_new_channels=False,
                        ),
                        "carol": ERPidChannelCollection(
                            liquidity_out=5_500_000,  # 2M + 1M + 2.5M = 5.5M
                            liquidity_in=5_000_000,  # 0 + 2M + 3M = 5M
                            private_only=False,  # One channel is public
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[11, 12, 13],
                            has_new_channels=False,
                        ),
                    },
                ),
            )
        )

        ########################################################
        ############# Pid Controller: Testcases ################
        ########################################################

        # Test concept for pid:
        # Main goal is to test the interplay between the aggregator the analytic
        # controllers.

        # 1. Two Channel Parties with two channels, two calls. First party uses default
        #    config for the spread controller and the second an individual config.
        # 2. Like 1. but the second channel party is new in the second call
        #    without last param for peer.
        # 3. Like 1. but the second channel party is new in the second call with
        #    last param for peer and age less equal than max_age_spread_hours.
        # 4. Like 1. but the second channel party is new in the second call with
        #    last param for peer and age greater than max_age_spread_hours.
        # 5. Like 1. but the second channel party is removed in the second call.
        # 6. Like 1. but k parameters changed for one party from one call to
        #    the other.
        # 7. Like 1. but for one party a change in the ref fee rate.

        # TODO:

        #    - tests with margin idiosyncratic
        #    - tests with spread level controller
        #    - tests with pin peer (add, change, remove, different methods)

        # Calculating the expected for the EwmaController calls. We have tested
        # the controller in separate unit tests. That's why it is ok to test
        # the interplay in this way.

        # Channel party 1: bob
        # We want to calculate some reference results for party bob with
        # bob_chan_1 and bob_chan_3
        # liquidity_in  = 1.5M + 1.5M + 0 + 0 = 3M
        # liquidity_out = 0.75M + 0.25M + 2M = 3M

        # first call for bob's channel
        call_1 = EwmaCall(3600, _calc_error(3_000_000, 3_000_000, 400_000), bob_ewma_2)
        spread_call_1 = call_ewma(60.0, [call_1]).control_variable

        # creating a copy for the second call. 0.5M more local liquidity and 0.5M
        # less remote liquidity.
        bob_chan_1_2 = copy.deepcopy(bob_chan_1)
        bob_chan_1_2.liquidity_out_settled_sat = 1_250_000
        bob_chan_1_2.liquidity_in_settled_sat = 1_000_000

        # second call for bob's channel
        call_2 = EwmaCall(2100, _calc_error(2_500_000, 3_500_000, 400_000), bob_ewma_2)
        spread_call_2 = call_ewma(60.0, [call_1, call_2]).control_variable

        # Chanel party 2: carol
        # We use carol_chan_2 and carol_chan_3
        # liquidity_in  = 2.0M + 3.0M = 5M
        # liquidity_out = 1.0M + 2.5M = 3.5M

        # first call for carol's channel
        # calculation of the default target
        # 6M * 0.4 + 8.5M * X = 8M => X = (8M - 2.4M) / 8.5M
        target_3 = (8_000_000 - 2_400_000) / 8.5
        call_3 = EwmaCall(
            3600, _calc_error(5_000_000, 3_500_000, target_3), default_ewma
        )
        spread_call_3 = call_ewma(360.0, [call_3]).control_variable

        # For the second call we keep the balances unchanged. But we have to use
        # a different target because the liquidity at bob's channels changed
        # 6M * 0.4 + 8.5M * X = 7.5M => X = (8M - 2.4M) / 8.5M
        target_4 = (7_500_000 - 2_400_000) / 8.5
        call_4 = EwmaCall(
            2100, _calc_error(5_000_000, 3_500_000, target_4), default_ewma
        )
        spread_call_4 = call_ewma(360.0, [call_3, call_4]).control_variable

        self.testcases_controller.append(
            TCasePidController(
                name="1",
                description="Two Channel Parties with two channels, two calls. First party uses default config for the spread controller and the second an individual config.",
                calls=[
                    # First Call
                    PidControllerCall(
                        timestamp=time_base,
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(None, None),
                        ewma_params_last={},
                        policies_last={},
                        block_height=840_000,
                        channels=[carol_chan_3, bob_chan_1, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_1, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_3, target=target_3
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_1 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_1),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_1 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_1),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_3 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_3),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_3 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_3),
                                ),
                            ],
                        ),
                    ),  # Second Call
                    PidControllerCall(
                        timestamp=time_base + timedelta(seconds=2100),
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(1, time_base),  # return first run.
                        ewma_params_last={},
                        policies_last={
                            1: policy_bob_last_1,
                            3: policy_bob_last_1,
                            12: policy_carol_last_1,
                            13: policy_carol_last_1,
                        },  # setting the last policies to avoid recalibration of the spread
                        block_height=840_000,
                        channels=[bob_chan_1_2, carol_chan_3, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_2, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_4, target=target_4
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1_2,
                                    force_update=False,
                                    fee_rate_ppm=int(
                                        spread_call_2 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_2),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=False,
                                    fee_rate_ppm=int(spread_call_2 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_2),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=False,
                                    fee_rate_ppm=int(
                                        spread_call_4 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_4),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=False,
                                    fee_rate_ppm=int(spread_call_4 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_4),
                                ),
                            ],
                        ),
                    ),
                ],
            )
        )

        # Default target for carol both channels in the first call
        target_5 = 5_000_000 / 8.5
        call_5 = EwmaCall(
            3600, _calc_error(5_000_000, 3_500_000, target_5), default_ewma
        )
        spread_call_5 = call_ewma(360.0, [call_5]).control_variable

        # The second the second call for carols channels is call_4. But because
        # if the first call we expect a different spread,
        spread_call_6 = call_ewma(360.0, [call_5, call_4]).control_variable

        # We use the same opening height as bob_chan_3. Then the opening is within
        # max_age_new_channels of 1000blocks at block 840_000.
        bob_chan_1_2.opening_height = 839_000

        # We also have to recalculate the expected result for bob in the second
        # call. Target is the same as in testcase 1.
        call_7 = EwmaCall(2100, _calc_error(2_500_000, 3_500_000, 400_000), bob_ewma_2)

        # The liquidity is more local, i.e. we start with 2000ppm local and a
        # spread of 2000ppm - 40ppm (margin) = 1960
        spread_call_7 = call_ewma(1960.0, [call_7]).control_variable

        self.testcases_controller.append(
            TCasePidController(
                name="2",
                description="Like 1. but bob is new in the second call without last param for peer and opening within max_age_new_channels.",
                calls=[
                    # First Call
                    PidControllerCall(
                        timestamp=time_base,
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(None, None),
                        ewma_params_last={},
                        policies_last={},
                        block_height=840_000,
                        channels=[carol_chan_3, carol_chan_2],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "carol": ERSpreadRateController(
                                    spread=spread_call_5, target=target_5
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_5 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_5),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_5 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_5),
                                ),
                            ],
                        ),
                    ),  # Second Call
                    PidControllerCall(
                        timestamp=time_base + timedelta(seconds=2100),
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(1, time_base),  # return first run.
                        ewma_params_last={},
                        policies_last={
                            12: policy_carol_last_1,
                            13: policy_carol_last_1,
                        },  # setting the last policies to avoid recalibration of the spread
                        block_height=840_000,
                        channels=[bob_chan_1_2, carol_chan_3, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_7, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_6, target=target_4
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_7 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_7),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_7 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_7),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=False,
                                    fee_rate_ppm=int(
                                        spread_call_6 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_6),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=False,
                                    fee_rate_ppm=int(spread_call_6 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_6),
                                ),
                            ],
                        ),
                    ),
                ],
            )
        )

        # We test that after an opening with ewma params not older than 2 hours
        # leads to a usage of the spread, i.e. the control_variable of 210 set
        # in bob_ewma_2
        pid_config_2.max_age_spread_hours = 2
        call_8 = EwmaCall(2100, _calc_error(2_500_000, 3_500_000, 400_000), bob_ewma_2)
        spread_call_8 = call_ewma(210.0, [call_8]).control_variable

        self.testcases_controller.append(
            TCasePidController(
                name="3",
                description="Like 1. but bob is new in the second call with last param for peer.",
                calls=[
                    # First Call
                    PidControllerCall(
                        timestamp=time_base,
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(None, None),
                        ewma_params_last={},
                        policies_last={},
                        block_height=840_000,
                        channels=[carol_chan_3, carol_chan_2],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "carol": ERSpreadRateController(
                                    spread=spread_call_5, target=target_5
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_5 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_5),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_5 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_5),
                                ),
                            ],
                        ),
                    ),  # Second Call
                    PidControllerCall(
                        timestamp=time_base + timedelta(seconds=2100),
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(1, time_base),  # return first run.
                        ewma_params_last={
                            "bob": (time_base + timedelta(seconds=-5100), bob_ewma_2)
                        },
                        policies_last={
                            12: policy_carol_last_1,
                            13: policy_carol_last_1,
                        },  # setting the last policies to avoid recalibration of the spread
                        block_height=840_000,
                        channels=[bob_chan_1_2, carol_chan_3, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_8, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_6, target=target_4
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_8 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_8),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_8 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_8),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=False,
                                    fee_rate_ppm=int(
                                        spread_call_6 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_6),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=False,
                                    fee_rate_ppm=int(spread_call_6 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_6),
                                ),
                            ],
                        ),
                    ),
                ],
            )
        )

        # Now the provided spread is older than 2h. Then we use fee_rate_new_local
        # again for initial spread calibration. Leads to the same result as
        # testcase 2.
        self.testcases_controller.append(
            TCasePidController(
                name="4",
                description="Like 1. but bob is new in the second call with last param for peer, but it is older than max_age_spread_hours.",
                calls=[
                    # First Call
                    PidControllerCall(
                        timestamp=time_base,
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(None, None),
                        ewma_params_last={},
                        policies_last={},
                        block_height=840_000,
                        channels=[carol_chan_3, carol_chan_2],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "carol": ERSpreadRateController(
                                    spread=spread_call_5, target=target_5
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_5 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_5),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_5 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_5),
                                ),
                            ],
                        ),
                    ),  # Second Call
                    PidControllerCall(
                        timestamp=time_base + timedelta(seconds=2100),
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(1, time_base),  # return first run.
                        ewma_params_last={
                            "bob": (time_base + timedelta(seconds=-5101), bob_ewma_2)
                        },  # spread 7201s (>2h) old.
                        policies_last={
                            12: policy_carol_last_1,
                            13: policy_carol_last_1,
                        },  # setting the last policies to avoid recalibration of the spread
                        block_height=840_000,
                        channels=[bob_chan_1_2, carol_chan_3, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_7, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_6, target=target_4
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_7 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_7),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_7 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_7),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=False,
                                    fee_rate_ppm=int(
                                        spread_call_6 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_6),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=False,
                                    fee_rate_ppm=int(spread_call_6 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_6),
                                ),
                            ],
                        ),
                    ),
                ],
            )
        )

        # Now we take 1. and remove bob's channels with the second call.
        # For carol we can use target_5, because balance is unchanged.

        call_8 = EwmaCall(
            2100, _calc_error(5_000_000, 3_500_000, target_5), default_ewma
        )

        spread_call_8 = call_ewma(360.0, [call_3, call_8]).control_variable

        self.testcases_controller.append(
            TCasePidController(
                name="5",
                description="Two Channel Parties with two channels, two calls. bob is removed with the second call.",
                calls=[
                    # First Call
                    PidControllerCall(
                        timestamp=time_base,
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(None, None),
                        ewma_params_last={},
                        policies_last={},
                        block_height=840_000,
                        channels=[carol_chan_3, bob_chan_1, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_1, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_3, target=target_3
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_1 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_1),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_1 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_1),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_3 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_3),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_3 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_3),
                                ),
                            ],
                        ),
                    ),  # Second Call
                    PidControllerCall(
                        timestamp=time_base + timedelta(seconds=2100),
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(1, time_base),  # return first run.
                        ewma_params_last={},
                        policies_last={
                            1: policy_bob_last_1,
                            3: policy_bob_last_1,
                            12: policy_carol_last_1,
                            13: policy_carol_last_1,
                        },  # setting the last policies to avoid recalibration of the spread
                        block_height=840_000,
                        channels=[carol_chan_3, carol_chan_2],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "carol": ERSpreadRateController(
                                    spread=spread_call_8, target=target_5
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=False,
                                    fee_rate_ppm=int(
                                        spread_call_8 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_8),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=False,
                                    fee_rate_ppm=int(spread_call_8 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_8),
                                ),
                            ],
                        ),
                    ),
                ],
            )
        )

        # We change the EwmaControllerParams between the calls for bob now.
        # Starting with call_1 again.
        # call_9 is like call_2 but with bob_ewma_2 instead of bob_ewma_3
        call_9 = EwmaCall(2100, _calc_error(2_500_000, 3_500_000, 400_000), bob_ewma_3)

        # the expected result for bob after the second call.
        spread_call_9 = call_ewma(60, [call_1, call_9]).control_variable

        self.testcases_controller.append(
            TCasePidController(
                name="6",
                description="Like 1. but k parameters changed for one party from one call to the other.",
                calls=[
                    # First Call
                    PidControllerCall(
                        timestamp=time_base,
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(None, None),
                        ewma_params_last={},
                        policies_last={},
                        block_height=840_000,
                        channels=[carol_chan_3, bob_chan_1, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_1, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_3, target=target_3
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_1 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_1),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_1 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_1),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_3 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_3),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_3 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_3),
                                ),
                            ],
                        ),
                    ),  # Second Call
                    PidControllerCall(
                        timestamp=time_base + timedelta(seconds=2100),
                        config=pid_config_4,  # config with the changed controller params
                        pid_run_last=(1, time_base),  # return first run.
                        ewma_params_last={},
                        policies_last={
                            1: policy_bob_last_1,
                            3: policy_bob_last_1,
                            12: policy_carol_last_1,
                            13: policy_carol_last_1,
                        },  # setting the last policies to avoid recalibration of the spread
                        block_height=840_000,
                        channels=[bob_chan_1_2, carol_chan_3, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_9, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_4, target=target_4
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1_2,
                                    force_update=False,
                                    fee_rate_ppm=int(
                                        spread_call_9 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_9),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=False,
                                    fee_rate_ppm=int(spread_call_9 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_9),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=False,
                                    fee_rate_ppm=int(
                                        spread_call_4 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_4),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=False,
                                    fee_rate_ppm=int(spread_call_4 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_4),
                                ),
                            ],
                        ),
                    ),
                ],
            )
        )

        # Creating a copy of bob_chan_1_2 and lowering the fee rate from 100 ppm
        # to 50 ppm.
        bob_chan_1_3 = copy.deepcopy(bob_chan_1_2)
        bob_chan_1_3.policy_local.fee_rate_ppm = 50  # type: ignore

        # Spread is recalibrated to 10 ppm (margin at 40 ppm) before second call.
        # call_10 is like call_2 but with a control variable of ten 10 for
        # for second call
        call_10 = EwmaCall(
            2100,
            _calc_error(2_500_000, 3_500_000, 400_000),
            bob_ewma_2,
            control_variable=10,
        )

        spread_call_10 = call_ewma(60, [call_1, call_10]).control_variable

        self.testcases_controller.append(
            TCasePidController(
                name="1",
                description="Like 1. but for one party a change in the ref fee rate.",
                calls=[
                    # First Call
                    PidControllerCall(
                        timestamp=time_base,
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(None, None),
                        ewma_params_last={},
                        policies_last={},
                        block_height=840_000,
                        channels=[carol_chan_3, bob_chan_1, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_1, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_3, target=target_3
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_1 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_1),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_1 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_1),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=True,
                                    fee_rate_ppm=int(
                                        spread_call_3 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_3),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=True,
                                    fee_rate_ppm=int(spread_call_3 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_3),
                                ),
                            ],
                        ),
                    ),  # Second Call
                    PidControllerCall(
                        timestamp=time_base + timedelta(seconds=2100),
                        config=pid_config_2,  # uses an individual spread controller config for bob and default config for carol
                        pid_run_last=(1, time_base),  # return first run.
                        ewma_params_last={},
                        policies_last={
                            1: policy_bob_last_1,
                            3: policy_bob_last_1,
                            12: policy_carol_last_1,
                            13: policy_carol_last_1,
                        },  # setting the last policies to avoid recalibration of the spread
                        block_height=840_000,
                        channels=[bob_chan_1_3, carol_chan_3, carol_chan_2, bob_chan_3],
                        expected_result=ERPidControllerCall(
                            margin_rate=40,  # Equals initial margin
                            spread_controller_results={
                                "bob": ERSpreadRateController(
                                    spread=spread_call_10, target=400_000
                                ),
                                "carol": ERSpreadRateController(
                                    spread=spread_call_4, target=target_4
                                ),
                            },
                            policy_proposals=[
                                PolicyProposal(
                                    channel=bob_chan_1_3,
                                    force_update=True,  # because of ref rate change
                                    fee_rate_ppm=int(
                                        spread_call_10 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_10),
                                ),
                                PolicyProposal(
                                    channel=bob_chan_3,
                                    force_update=True,  # because of ref rate change
                                    fee_rate_ppm=int(spread_call_10 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_10),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_2,
                                    force_update=False,
                                    fee_rate_ppm=int(
                                        spread_call_4 + 40
                                    ),  # spread + 40ppm margin
                                    inbound_fee_rate_ppm=int(-spread_call_4),
                                ),
                                PolicyProposal(
                                    channel=carol_chan_3,
                                    force_update=False,
                                    fee_rate_ppm=int(spread_call_4 + 40),
                                    inbound_fee_rate_ppm=int(-spread_call_4),
                                ),
                            ],
                        ),
                    ),
                ],
            )
        )

    def test_aggregator(self):
        """
        Runs all testcases for the aggregator.
        """

        for t in self.testcases_aggregator:
            # shortcut for expected result
            e = t.expected_result

            # creating our test object for this testcase
            agg = ChannelAggregator.from_channels(
                config=t.config,
                policies_last=t.policies_last,
                block_height=t.block_height,
                channels=t.channels,
            )

            # Body of the message.
            msg = f"failed testcase: {t=}, aggregator: {agg=}"

            pub_keys: set[str] = set()

            if not isinstance(e.channel_collection, NoExpectedResult):
                # Union set of the pub_keys
                pub_keys = agg.channel_collections.keys() | e.channel_collection.keys()

            if e.target_default is not NO_RES:
                self.assertEqual(agg.target_default, e.target_default, msg)

            # Loop over the pub_keys and assert the channel collections.
            for pub_key in pub_keys:
                agg_col = agg.channel_collections.get(pub_key)

                msg = f"failed testcase: {t=}, {pub_key=}, aggregator: {agg=}"
                # Check that we have a ChannelCollection and not a None Object
                self.assertIsInstance(agg_col, ChannelCollection, msg)

                e_col = e.channel_collection.get(pub_key)  # type: ignore

                # Checks that we have an expected result. If not the aggregator
                # creates an unexpected result.
                self.assertIsInstance(e_col, ERPidChannelCollection, msg)

                self._assert_channel_collections(agg_col, e_col, msg)  # type: ignore

    def _assert_channel_collections(
        self, col: ChannelCollection, e_col: ERPidChannelCollection, msg: str
    ):
        """
        Helper method to validate a ChannelCollection using the expected results.
        """

        # Testing attributes
        self.assertEqual(col.liquidity_out, e_col.liquidity_out, msg)
        self.assertEqual(col.liquidity_in, e_col.liquidity_in, msg)
        self.assertEqual(col.private_only, e_col.private_only, msg)
        self.assertEqual(col.ref_fee_rate, e_col.ref_fee_rate, msg)
        self.assertEqual(col.ref_fee_rate_last, e_col.ref_fee_rate_last, msg)
        self.assertEqual(col.ref_fee_rate_changed, e_col.ref_fee_rate_changed, msg)
        self.assertEqual(col.has_new_channels, e_col.has_new_channels, msg)

        # Testing the ids of the channels in the collection
        col_chan_ids = sorted([c.chan_id for c in col.pid_channels()])
        e_col_chan_ids = sorted(e_col.chan_ids)

        self.assertEqual(col_chan_ids, e_col_chan_ids, msg)

    def test_pid_controller(self):

        for t in self.testcases_controller:
            # Creating a new pid controller using the first config to init the
            # margin controller.

            pid_store = MagicMock()
            # patch to mock pid_run_last which is executed in PidController.__init__
            pid_store.pid_run_last = lambda: t.calls[0].pid_run_last
            controller = PidController(
                pid_store=pid_store,
                ln_store=MagicMock(),
                config=t.calls[0].config,
            )

            # message body
            msg = f"{t.name=}; {t.description}"

            # Calling the controller
            for i, c in enumerate(t.calls):
                msgcall = f"call {i=}; " + msg

                # Create a new lncache with our testdata.
                lncache = LightningCache(
                    lnclient=_new_mock_lnclient(
                        block_height=c.block_height, channels=c.channels
                    )
                )

                controller.ln_store.local_policies = MagicMock(
                    return_value=c.policies_last
                )

                controller.pid_store.pid_run_last = MagicMock(
                    return_value=c.pid_run_last
                )

                # Callable for mocking ewma_params_last_by_peer, it returns,
                # the value of the dict ewma_params_last and if not defined
                # (None, None) as default.
                def params_last(
                    peer_pub_key: str,
                ) -> tuple[None, None] | tuple[datetime, EwmaControllerParams]:
                    return c.ewma_params_last.get(peer_pub_key, (None, None))

                controller.pid_store.ewma_params_last_by_peer = params_last

                # Now all is mocked and we can call the controller.
                controller(c.config, lncache, c.timestamp)

                # We can proceed with the next call if there is no expected result.
                if isinstance(c.expected_result, NoExpectedResult):
                    continue

                self._assert_pid_controller_call(controller, c.expected_result, msgcall)

    def _assert_pid_controller_call(
        self, c: PidController, e: ERPidControllerCall, msg: str
    ):
        """
        Validation of a pid controller after a call using the expected results.
        """

        self.assertEqual(c.margin_controller.margin, e.margin_rate, msg)

        # Assert the spread rate controller. First we determine the union set
        # of pub_keys
        pub_keys = c.spread_controller_map.keys() | e.spread_controller_results.keys()
        for pub_key in pub_keys:
            msgpub = f"{pub_key=}; " + msg

            s = c.spread_controller_map.get(pub_key)
            # Ensures also that it is not None.
            self.assertIsInstance(s, SpreadController, msgpub)

            r = e.spread_controller_results.get(pub_key)
            # Ensures also that it is not None.
            self.assertIsInstance(r, ERSpreadRateController, msgpub)

            self._assert_spread_controller(s, r, msgpub)  # type: ignore

        # Create a dict of the generated PolicyProposal. And afterward a set
        # of all chan_points.
        props: dict[str, PolicyProposal] = {
            p.channel.chan_point: p for p in c.policy_proposals()
        }
        e_props: dict[str, PolicyProposal] = {
            p.channel.chan_point: p for p in e.policy_proposals
        }

        # Assert the the PolicyProposal's for each chan_point now.
        chan_points = props.keys() | e_props.keys()
        for chan in chan_points:
            msgchan = f"{chan=}; " + msg
            p = props.get(chan)
            self.assertIsInstance(p, PolicyProposal, msgchan)

            r = e_props.get(chan)
            self.assertIsInstance(r, PolicyProposal, msgchan)

            # We assert the whole PolicyProposal objects here.
            self.assertEqual(p, r, msgchan)

    def _assert_spread_controller(
        self, con: SpreadController, e_con: ERSpreadRateController, msg: str
    ) -> None:
        self.assertEqual(con.spread, e_con.spread, msg)
        self.assertEqual(con.target, e_con.target, msg)

    def test_calc_error(self):
        testcases: list[TCasePidError] = []

        testcases.append(
            TCasePidError(
                name="1",
                description="zero liquidity",
                liquidity_in=0,
                liquidity_out=0,
                target=100_000,
                expected_error=0,
            )
        )

        testcases.append(
            TCasePidError(
                name="2",
                description="liquidity_in over target",
                liquidity_in=4_000_000,
                liquidity_out=2_000_000,
                target=400_000,
                # ratio = 4/6; set_point=4/10 => ratio>set_point
                # error = 1/2 / (1 - 4/10) * (4/6 - 4/10) + 0
                #       = 1/2 * 5/3 * 16/60 = 80/360 = 2/9
                expected_error=2 / 9,
            )
        )

        testcases.append(
            TCasePidError(
                name="3",
                description="liquidity_in below target",
                liquidity_in=1_000_000,
                liquidity_out=11_000_000,
                target=400_000,
                # ratio = 1/12; set_point=4/10 => ratio>set_point
                # error = 1/2 / (4/10 - 0) * (1/12 - 4/10) + 0
                #       = 1/2 * 5/2 * (-38)/120 = -190/480 = -19/48
                expected_error=-19 / 48,
            )
        )

        testcases.append(
            TCasePidError(
                name="4",
                description="liquidity_in == 0",
                liquidity_in=0,
                liquidity_out=11_000_000,
                target=400_000,
                expected_error=-1 / 2,
            )
        )

        testcases.append(
            TCasePidError(
                name="5",
                description="liquidity_out == 0",
                liquidity_in=11_000_000,
                liquidity_out=0,
                target=400_000,
                expected_error=1 / 2,
            )
        )

        for t in testcases:
            msg = f"{t.name=}; {t.description=}"
            err = _calc_error(t.liquidity_in, t.liquidity_out, t.target)

            # Compare on 7 decimal places
            self.assertAlmostEqual(err, t.expected_error, None, msg)
