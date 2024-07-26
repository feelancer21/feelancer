"""
1. Aggregation of the channels per peer.
2. Identifying the pid related channels (no private, but shadow channels allowed)
3. Calculation of the default target of the peer controller
4. Assigning the fee_rates from the config to new channel peers
5. Calculation of two fee_rate averages (1. local balance weighted,
   2. capacity * target weighed) for the margin controller error calculation
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generator, Iterable

if TYPE_CHECKING:
    from feelancer.lightning.client import Channel, ChannelPolicy

    from .data import PidConfig

PEER_TARGET_UNIT = 1_000_000
TARGET_DEFAULT = 500_000


class ChannelCollection:
    def __init__(
        self,
        min_height_new_channels: int,
        fee_rate_new_local: int,
        fee_rate_new_remote: int,
    ) -> None:
        self.channels: dict[int, Channel] = {}
        self.min_height_new_channels = min_height_new_channels
        self._ref_fee_rate: int | None = None
        self.ref_fee_rate_last: int | None = None
        self.fee_rate_new_local = fee_rate_new_local
        self.fee_rate_new_remote = fee_rate_new_remote
        self.private_only = True
        self._new_channels: list[int] = []

    def add_channel(self, channel: Channel, policy_last: ChannelPolicy | None) -> None:
        self.channels[channel.chan_id] = channel
        self._pid_channels: list[Channel] | None = None

        # If there is one public we consider the whole collection as public,
        # because each channel can be used for routing as shadow channel.
        # Hence we want to take the liquidity in these channels into account.
        if not channel.private:
            self.private_only = False

        if not channel.policy_local:
            return None

        # We set ref_fee_rate_last if there is a policy. Usually all policies with
        # one peer should have the same fee rates. But to be safe we are using
        # the lowest fee_rate.
        if policy_last:
            if (
                not self.ref_fee_rate_last
                or policy_last.fee_rate_ppm < self.ref_fee_rate_last
            ):
                self.ref_fee_rate_last = policy_last.fee_rate_ppm

        # If we had no policy from the last run and if the channel is not too
        # old then we consider it as new channel.
        if not policy_last and channel.opening_height >= self.min_height_new_channels:
            self._new_channels.append(channel.chan_id)
            return None

        # We are updating the ref fee rate now and are looking for the lowest
        # fee rate over all channels in this collection
        if (
            not self._ref_fee_rate
            or channel.policy_local.fee_rate_ppm < self._ref_fee_rate
        ):
            self._ref_fee_rate = channel.policy_local.fee_rate_ppm

    @property
    def liquidity_out(self) -> float:
        return sum(
            channel.liquidity_out_pending_sat + channel.liquidity_out_settled_sat
            for channel in self.pid_channels()
        )

    @property
    def liquidity_in(self) -> float:
        return sum(
            channel.liquidity_in_pending_sat + channel.liquidity_in_settled_sat
            for channel in self.pid_channels()
        )

    @property
    def ref_fee_rate(self) -> int:
        if self._ref_fee_rate is not None:
            return self._ref_fee_rate

        if self.liquidity_out > self.liquidity_in:
            self._ref_fee_rate = self.fee_rate_new_local
        else:
            self._ref_fee_rate = self.fee_rate_new_remote

        return self._ref_fee_rate

    @property
    def ref_fee_rate_changed(self) -> bool:
        ref = self.ref_fee_rate_last
        if not ref:
            return True
        return ref != self.ref_fee_rate

    def pid_channels(self) -> Generator[Channel, None, None]:
        if self.private_only:
            return None

        if not self._pid_channels:
            self._pid_channels = self._get_pid_channels()

        for channel in self._pid_channels:
            yield channel

    def _get_pid_channels(self) -> list[Channel]:
        """
        Determine all channels which have to be modelled.
        We are returning None if there are only private channel in this
        collection.
        Public channels without any policy at the moment are not returned too,
        which can be the case if a channel was opened lately.
        Otherwise the fee_rate of the channel has to be equal to the ref_fee_rate,
        which is the minimum fee_rate of all fee_rates.

        """
        pid_channels: list[Channel] = []
        if self.private_only:
            return pid_channels

        for channel in self.channels.values():
            # Skipping public channels without policy
            if not channel.private and not channel.policy_local:
                continue

            # Remaining channels without policy are private, shadow channels.
            if not channel.policy_local:
                pid_channels.append(channel)
                continue

            if channel.chan_id in self._new_channels:
                pid_channels.append(channel)
                continue

            if channel.policy_local.fee_rate_ppm == self._ref_fee_rate:
                pid_channels.append(channel)
                continue

        return pid_channels


class ChannelAggregator:
    def __init__(
        self,
        config: PidConfig,
    ) -> None:
        self.config = config
        self.channel_collections: dict[str, ChannelCollection] = {}

        self._target_default: float | None = None

    @classmethod
    def from_channels(
        cls,
        config: PidConfig,
        policies_last: dict[int, ChannelPolicy],
        block_height: int,
        channels: Iterable[Channel],
    ) -> ChannelAggregator:
        aggregator = cls(config)
        min_height_new_channel = block_height - config.max_age_new_channels
        for channel in channels:
            aggregator.add_channel(
                channel=channel,
                policy_last=policies_last.get(channel.chan_id),
                min_height_new_channels=min_height_new_channel,
            )

        return aggregator

    def add_channel(
        self,
        channel: Channel,
        policy_last: ChannelPolicy | None,
        min_height_new_channels: int,
    ) -> None:
        self._target_default = None

        pub_key = channel.pub_key

        if channel.pub_key in self.config.exclude_pubkeys:
            return None

        if channel.chan_id in self.config.exclude_chanids:
            return None

        if not (col := self.channel_collections.get(pub_key)):
            peer_config = self.config.peer_config(pub_key)

            col = self.channel_collections[pub_key] = ChannelCollection(
                min_height_new_channels=min_height_new_channels,
                fee_rate_new_local=peer_config.fee_rate_new_local,
                fee_rate_new_remote=peer_config.fee_rate_new_remote,
            )

        col.add_channel(channel, policy_last)

    def pid_channels(self) -> Generator[Channel, None, None]:
        for col in self.channel_collections.values():
            for channel in col.pid_channels():
                yield channel

    def pid_collections(self) -> Generator[tuple[str, ChannelCollection], None, None]:
        for pub_key, collection in self.channel_collections.items():
            if collection.private_only:
                continue
            yield pub_key, collection

    @property
    def target_default(self) -> float:
        if self._target_default:
            return self._target_default

        sum_local: int = 0

        sum_liquidity: int = 0
        sum_liquidity_known_target: int = 0
        sum_liquidity_target: int = 0

        for channel in self.pid_channels():
            local = (
                channel.liquidity_out_pending_sat + channel.liquidity_out_settled_sat
            )
            remote = channel.liquidity_in_pending_sat + channel.liquidity_in_settled_sat
            sum_local += local

            sum_liquidity += local + remote

            if (peer_config := self.config.peers.get(channel.pub_key)) and (
                peer_config.target
            ):
                sum_liquidity_known_target += local + remote
                sum_liquidity_target += (local + remote) * peer_config.target

        try:
            self._target_default = (
                PEER_TARGET_UNIT * (sum_liquidity - sum_local) - sum_liquidity_target
            ) / (sum_liquidity - sum_liquidity_known_target)

        except ZeroDivisionError:
            # This case should only happen when sum_liquidity is equal to
            # sum_liquidity_known_target. Then we don't really need target_default.
            self._target_default = TARGET_DEFAULT

        return self._target_default
