from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import cast
from unittest.mock import MagicMock

from feelancer.lightning.client import Channel, ChannelPolicy
from feelancer.pid.aggregator import ChannelAggregator, ChannelCollection
from feelancer.pid.controller import _calc_error
from feelancer.pid.data import PidConfig, PidSpreadControllerConfig


def _new_mock_channel_policy(fee_rate_ppm: int) -> ChannelPolicy:
    p = cast(ChannelPolicy, MagicMock())
    p.fee_rate_ppm = fee_rate_ppm
    return p


def _new_mock_channel(
    pub_key: str,
    chan_id: int,
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
    c.capacity_sat = capacity
    c.private = private
    c.opening_height = opening_height
    c.liquidity_in_settled_sat = liq_in
    c.liquidity_out_settled_sat = liq_out
    c.liquidity_in_pending_sat = liq_in_pen
    c.liquidity_out_pending_sat = liq_out_pen
    c.policy_local = _new_mock_channel_policy(fee_rate_ppm)
    return c


def _new_mock_pid_config(
    exclude_pubkeys: list[str],
    exclude_chanid: list[int],
    max_age_new_channels: int,
    config: dict[str, PidSpreadControllerConfig],
    default_config: PidSpreadControllerConfig,
) -> PidConfig:

    c = cast(PidConfig, MagicMock())
    c.exclude_pubkeys = exclude_pubkeys
    c.exclude_chanids = exclude_chanid
    c.max_age_new_channels = max_age_new_channels

    # peer_config returns the value of dict config, and uses default_config
    # as default.
    def peer_config(pub_key: str) -> PidSpreadControllerConfig:
        return config.get(pub_key, default_config)

    c.peer_config = peer_config
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


@dataclass
class ERPidAggregator:
    """Expected result for a aggregator."""

    target_default: float | NoExpectedResult
    channel_collection: dict[str, ERPidChannelCollection] | NoExpectedResult


@dataclass
class TCasePidAggregator:
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

        configs_1: dict[str, PidSpreadControllerConfig] = {}
        exclude_pubkeys_1: list[str] = []
        exclude_chanid_1: list[int] = []

        exclude_pubkeys_2: list[str] = ["carol"]
        exclude_chanid_2: list[int] = [2]

        max_age_new_channels = 1000

        default_config = PidSpreadControllerConfig(
            fee_rate_new_local=1000, fee_rate_new_remote=210, target=None
        )

        configs_2 = {
            "bob": PidSpreadControllerConfig(
                fee_rate_new_local=2000, fee_rate_new_remote=420, target=400_000
            )
        }

        pid_config = _new_mock_pid_config(
            exclude_pubkeys=exclude_pubkeys_1,
            exclude_chanid=exclude_chanid_1,
            max_age_new_channels=max_age_new_channels,
            config=configs_1,
            default_config=default_config,
        )

        pid_config_2 = _new_mock_pid_config(
            exclude_pubkeys=exclude_pubkeys_1,
            exclude_chanid=exclude_chanid_1,
            max_age_new_channels=max_age_new_channels,
            config=configs_2,
            default_config=default_config,
        )

        pid_config_3 = _new_mock_pid_config(
            exclude_pubkeys=exclude_pubkeys_2,
            exclude_chanid=exclude_chanid_2,
            max_age_new_channels=max_age_new_channels,
            config=configs_1,
            default_config=default_config,
        )

        bob_chan_1 = _new_mock_channel(
            pub_key="bob",
            chan_id=1,
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
                        ),
                        "carol": ERPidChannelCollection(
                            liquidity_out=0,  # 0 because it is private only
                            liquidity_in=0,
                            private_only=True,
                            ref_fee_rate=400,
                            ref_fee_rate_last=400,
                            ref_fee_rate_changed=False,
                            chan_ids=[],  # empty because of private only
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
                        ),
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
                            ],
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
                        ),
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
                            ],
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
                        ),
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
                            ],
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
                        ),
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
                            ],
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
                        ),
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
                            ],
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
                        ),
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
                            ],
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
                        ),
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
                            ],
                        ),
                    },
                ),
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

        # Testing the ids of the channels in the collection
        col_chan_ids = sorted([c.chan_id for c in col.pid_channels()])
        e_col_chan_ids = sorted(e_col.chan_ids)

        self.assertEqual(col_chan_ids, e_col_chan_ids, msg)

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
