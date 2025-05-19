from __future__ import annotations

import copy
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import MagicMock

from feelancer.config import FeelancerPeersConfig
from feelancer.lightning.chan_updates import PolicyProposal, _new_update_policies
from feelancer.lightning.client import Channel, ChannelPolicy


@dataclass
class TCaseCreateUpdatePolicies:
    # name of the testcase
    name: str

    # description of the testcase
    description: str

    # inputs data for the test
    proposals: list[PolicyProposal]
    pub_key: str
    peer_config: FeelancerPeersConfig
    timenow: datetime

    # expected results to be assert
    expected_results: dict[str, ChannelPolicy]


def _new_mock_channel(
    fee_rate_ppm: int,
    base_fee_msat: int,
    time_lock_delta: int,
    inbound_fee_rate_ppm: int,
    inbound_base_fee_msat: int,
    last_update: datetime,
    chan_point: str,
    pub_key: str,
) -> Channel:
    """
    Creates a mock channel with the given parameters and returns it.
    """

    # Create a mock policy for the channel
    mock_policy: ChannelPolicy = cast(ChannelPolicy, MagicMock())
    mock_policy.fee_rate_ppm = fee_rate_ppm
    mock_policy.base_fee_msat = base_fee_msat
    mock_policy.time_lock_delta = time_lock_delta
    mock_policy.inbound_fee_rate_ppm = inbound_fee_rate_ppm
    mock_policy.inbound_base_fee_msat = inbound_base_fee_msat
    mock_policy.last_update = int(last_update.timestamp())

    # Create a mock channel and attach the policy
    mock_channel: Channel = cast(Channel, MagicMock())
    mock_channel.policy_local = mock_policy
    mock_channel.chan_point = chan_point
    mock_channel.pub_key = pub_key

    return mock_channel


def _new_expected_policy(
    channel: Channel, fee_rate_ppm: int, inbound_fee_rate_ppm: int
) -> ChannelPolicy:
    policy = copy.copy(channel.policy_local)

    # should not happen at the moment, because those channels are skipped
    # in the aggregator
    if not policy:
        raise ValueError

    policy.fee_rate_ppm = fee_rate_ppm
    policy.inbound_fee_rate_ppm = inbound_fee_rate_ppm

    return policy


class TestCreateUpdatePolicies(unittest.TestCase):

    def setUp(self):

        self.testcases: list[TCaseCreateUpdatePolicies] = []

        # base time for the last_update
        time_base = datetime(2021, 1, 1, 0, 0, 0)

        # config for almost most of the test
        mock_peer_config = cast(FeelancerPeersConfig, MagicMock())
        mock_peer_config.fee_rate_max = 2000
        mock_peer_config.fee_rate_min = 100
        mock_peer_config.fee_rate_ppm_min_up = 10
        mock_peer_config.fee_rate_ppm_min_down = 5
        mock_peer_config.inbound_fee_rate_max = 100
        mock_peer_config.inbound_fee_rate_min = -1000
        mock_peer_config.inbound_fee_rate_ppm_min_up = 30
        mock_peer_config.inbound_fee_rate_ppm_min_down = 15
        mock_peer_config.min_seconds = 3600

        # copy of the config which enables us to set the fee rates to zero
        mock_peer_config_2 = copy.copy(mock_peer_config)
        mock_peer_config_2.fee_rate_min = 0
        mock_peer_config_2.inbound_fee_rate_max = 0

        bob_chan_1 = _new_mock_channel(
            fee_rate_ppm=1000,
            base_fee_msat=1,
            time_lock_delta=144,
            inbound_fee_rate_ppm=-200,
            inbound_base_fee_msat=1,
            last_update=time_base,
            chan_point="bob_chan_1",
            pub_key="bob",
        )

        # bob_chan_2 has the same values than bob_chan_1 but its last update
        # is 30 minutes later
        bob_chan_2 = _new_mock_channel(
            fee_rate_ppm=1000,
            base_fee_msat=1,
            time_lock_delta=144,
            inbound_fee_rate_ppm=-200,
            inbound_base_fee_msat=1,
            last_update=time_base + timedelta(minutes=30),
            chan_point="bob_chan_2",
            pub_key="bob",
        )

        # bob_chan_3 has different policy values than the others. last_update
        # 15 minutes after bob_chan_1
        bob_chan_3 = _new_mock_channel(
            fee_rate_ppm=1999,
            base_fee_msat=0,
            time_lock_delta=288,
            inbound_fee_rate_ppm=0,
            inbound_base_fee_msat=0,
            last_update=time_base + timedelta(minutes=15),
            chan_point="bob_chan_3",
            pub_key="bob",
        )

        # bob_chan_4 is a channel where the outbound and inbound needs a down
        # movement less than our min_down to hit the min fee rates
        bob_chan_4 = _new_mock_channel(
            fee_rate_ppm=102,
            base_fee_msat=1,
            time_lock_delta=144,
            inbound_fee_rate_ppm=-997,
            inbound_base_fee_msat=1,
            last_update=time_base,
            chan_point="bob_chan_4",
            pub_key="bob",
        )

        # bob_chan_5 is a channel where the outbound and inbound needs a down
        # movement less than our min_down to hit the min fee rates
        bob_chan_5 = _new_mock_channel(
            fee_rate_ppm=1995,
            base_fee_msat=1,
            time_lock_delta=144,
            inbound_fee_rate_ppm=98,
            inbound_base_fee_msat=1,
            last_update=time_base,
            chan_point="bob_chan_5",
            pub_key="bob",
        )

        ###############################################
        ##### Testcases with one channel per peer #####
        ###############################################

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="1",
                description="fees within min/max bounds",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1500,
                        inbound_fee_rate_ppm=-200,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1500, -200)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="2",
                description="fees exceeds fee_rate_max",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=2100,
                        inbound_fee_rate_ppm=-200,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 2000, -200)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="3",
                description="fees exceeds fee_rate_min",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=50,
                        inbound_fee_rate_ppm=-200,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 100, -200)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="4",
                description="fees exceeds inbound_fee_rate_max",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1500,
                        inbound_fee_rate_ppm=150,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1500, 100)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="4",
                description="fees exceeds inbound_fee_rate_max",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1500,
                        inbound_fee_rate_ppm=-1005,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1500, -1000)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="5",
                description="both fee rate delta below min_up",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1008,
                        inbound_fee_rate_ppm=-171,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={},
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="6",
                description="outbound fee rate delta below min_up, but inbound fee rate above",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1008,
                        inbound_fee_rate_ppm=-169,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1000, -169)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="6",
                description="inbound fee rate delta below min_up, but outbound fee rate above",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1011,
                        inbound_fee_rate_ppm=-185,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1011, -200)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="7",
                description="both fee rate delta below min_down",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=996,
                        inbound_fee_rate_ppm=-214,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={},
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="8",
                description="outbound fee rate delta below min_down, but inbound fee rate above",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=996,
                        inbound_fee_rate_ppm=-215,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1000, -215)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="9",
                description="inbound fee rate delta below min_down, but outbound fee rate above",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=995,
                        inbound_fee_rate_ppm=-214,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 995, -200)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="10",
                description="last update before 1 hour",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=995,
                        inbound_fee_rate_ppm=-214,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(seconds=3599),
                expected_results={},
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="11",
                description="set both fee rates to 0",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=0,
                        inbound_fee_rate_ppm=0,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config_2,
                timenow=time_base + timedelta(seconds=3600),
                expected_results={"bob_chan_1": _new_expected_policy(bob_chan_1, 0, 0)},
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="12",
                description="both fee deltas below min_down with hitting the min.",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_4,
                        fee_rate_ppm=0,
                        inbound_fee_rate_ppm=-10000,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_4": _new_expected_policy(bob_chan_4, 100, -1000)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="13",
                description="both fee deltas below min_up with hitting the max.",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_5,
                        fee_rate_ppm=4000,
                        inbound_fee_rate_ppm=200,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_5": _new_expected_policy(bob_chan_5, 2000, 100)
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="14",
                description="last update before 1 hour; but we force the update",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        force_update=True,
                        fee_rate_ppm=995,
                        inbound_fee_rate_ppm=-214,
                    )
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(seconds=3599),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 995, -200)
                },
            )
        )
        #####################################################
        ##### Testcases with multiple channels per peer #####
        #####################################################

        # If we have multiple channels, we test that a significant change in one
        # or multiple channels triggers an update in all channels for the
        # associated policy side (outbound vs inbound).
        # We test it with different proposals for channel. Then it works also
        # if our tool advances the same policy for all channels with one peer.

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="101",
                description="all of three channels require an update with the same proposals",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1500,
                        inbound_fee_rate_ppm=-400,
                    ),
                    PolicyProposal(
                        channel=bob_chan_2,
                        fee_rate_ppm=1600,
                        inbound_fee_rate_ppm=-450,
                    ),
                    PolicyProposal(
                        channel=bob_chan_3,
                        fee_rate_ppm=1700,
                        inbound_fee_rate_ppm=-500,
                    ),
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1500, -400),
                    "bob_chan_2": _new_expected_policy(bob_chan_2, 1600, -450),
                    "bob_chan_3": _new_expected_policy(bob_chan_3, 1700, -500),
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="102",
                description="no channel requires an update; deltas all below min/max up/down",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1000,
                        inbound_fee_rate_ppm=-200,
                    ),
                    PolicyProposal(
                        channel=bob_chan_2,
                        fee_rate_ppm=1009,
                        inbound_fee_rate_ppm=-214,
                    ),
                    PolicyProposal(
                        channel=bob_chan_3,
                        fee_rate_ppm=1995,
                        inbound_fee_rate_ppm=29,
                    ),
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={},
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="103",
                description="2nd channel triggers update of all outbound fees; other deltas all below min/max up/down",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1000,
                        inbound_fee_rate_ppm=-200,
                    ),
                    PolicyProposal(
                        channel=bob_chan_2,
                        fee_rate_ppm=1010,
                        inbound_fee_rate_ppm=-214,
                    ),
                    PolicyProposal(
                        channel=bob_chan_3,
                        fee_rate_ppm=1995,
                        inbound_fee_rate_ppm=29,
                    ),
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1000, -200),
                    "bob_chan_2": _new_expected_policy(bob_chan_2, 1010, -200),
                    "bob_chan_3": _new_expected_policy(bob_chan_3, 1995, 0),
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="104",
                description="2nd channel triggers update of all inbound fees; other deltas all below min/max up/down",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1000,
                        inbound_fee_rate_ppm=-200,
                    ),
                    PolicyProposal(
                        channel=bob_chan_2,
                        fee_rate_ppm=1009,
                        inbound_fee_rate_ppm=-215,
                    ),
                    PolicyProposal(
                        channel=bob_chan_3,
                        fee_rate_ppm=1995,
                        inbound_fee_rate_ppm=29,
                    ),
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1000, -200),
                    "bob_chan_2": _new_expected_policy(bob_chan_2, 1000, -215),
                    "bob_chan_3": _new_expected_policy(bob_chan_3, 1999, 29),
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="105",
                description="2nd channel triggers update of all inbound and outbound fees; other deltas all below min/max up/down",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1000,
                        inbound_fee_rate_ppm=-200,
                    ),
                    PolicyProposal(
                        channel=bob_chan_2,
                        fee_rate_ppm=1010,
                        inbound_fee_rate_ppm=-215,
                    ),
                    PolicyProposal(
                        channel=bob_chan_3,
                        fee_rate_ppm=1995,
                        inbound_fee_rate_ppm=29,
                    ),
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                timenow=time_base + timedelta(hours=2),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1000, -200),
                    "bob_chan_2": _new_expected_policy(bob_chan_2, 1010, -215),
                    "bob_chan_3": _new_expected_policy(bob_chan_3, 1995, 29),
                },
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="105",
                description="2nd channel triggers update of all inbound and outbound fees; but timestamp too recent.",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1000,
                        inbound_fee_rate_ppm=-200,
                    ),
                    PolicyProposal(
                        channel=bob_chan_2,
                        fee_rate_ppm=1010,
                        inbound_fee_rate_ppm=-215,
                    ),
                    PolicyProposal(
                        channel=bob_chan_3,
                        fee_rate_ppm=1995,
                        inbound_fee_rate_ppm=29,
                    ),
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                # Less than one hour after our last_update of bob_chan_2.
                # With this setting all other channels has passed our min_seconds=3600.
                timenow=time_base + timedelta(minutes=30) + timedelta(seconds=3599),
                expected_results={},
            )
        )

        self.testcases.append(
            TCaseCreateUpdatePolicies(
                name="106",
                description="2nd channel triggers update of all inbound and outbound fees; timestamp too recent but force_update flat is set.",
                proposals=[
                    PolicyProposal(
                        channel=bob_chan_1,
                        fee_rate_ppm=1000,
                        inbound_fee_rate_ppm=-200,
                    ),
                    PolicyProposal(
                        channel=bob_chan_2,
                        force_update=True,
                        fee_rate_ppm=1010,
                        inbound_fee_rate_ppm=-215,
                    ),
                    PolicyProposal(
                        channel=bob_chan_3,
                        fee_rate_ppm=1995,
                        inbound_fee_rate_ppm=29,
                    ),
                ],
                pub_key="bob",
                peer_config=mock_peer_config,
                # Less than one hour after our last_update of bob_chan_2.
                # With this setting all other channels has passed our min_seconds=3600.
                timenow=time_base + timedelta(minutes=30) + timedelta(seconds=3599),
                expected_results={
                    "bob_chan_1": _new_expected_policy(bob_chan_1, 1000, -200),
                    "bob_chan_2": _new_expected_policy(bob_chan_2, 1010, -215),
                    "bob_chan_3": _new_expected_policy(bob_chan_3, 1995, 29),
                },
            )
        )

    def test_new_update_policies(self):

        for t in self.testcases:
            res = _new_update_policies(t.proposals, t.pub_key, t.peer_config, t.timenow)
            msg = f"failed testcase: {t=}, result: {res=}"
            self.assertIsInstance(res, dict)

            # Checking that both results have the same len.
            self.assertEqual(len(res), len(t.expected_results), msg)
            if len(res) == 0:
                continue

            for chan_point, exp_policy in t.expected_results.items():
                policy = res.get(chan_point)
                self.assertIsNotNone(policy, msg)
                self._assert_policy(policy, exp_policy, msg)  # type: ignore

    def _assert_policy(self, policy1: ChannelPolicy, policy2: ChannelPolicy, msg: str):
        self.assertEqual(policy1.fee_rate_ppm, policy2.fee_rate_ppm, msg)
        self.assertEqual(
            policy1.inbound_fee_rate_ppm, policy2.inbound_fee_rate_ppm, msg
        )
