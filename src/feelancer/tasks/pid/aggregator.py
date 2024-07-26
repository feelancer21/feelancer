"""
1. Aggregation of the channels per peer.
2. Identifying the pid related channels (no private, but shadow channels allowed)
3. Calculation of the default target of the peer controller
4. Assigning the feerates from the config to new channel peers
5. Calculation of two feerate averages (1. local balance weighted,
   2. capacity * target weighed) for the feelevel controller error calculation
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generator, Iterable

if TYPE_CHECKING:
    from feelancer.lightning.client import Channel, ChannelPolicy

    from .config import PidConfig

PEER_TARGET_UNIT = 1_000_000
TARGET_DEFAULT = 500_000


class ChannelCollection:
    def __init__(
        self,
        policies_end_last: dict[int, ChannelPolicy] | None,
        min_height_new_channels: int,
        feerate_new_local: int,
        feerate_new_remote: int,
    ) -> None:
        self.channels: dict[int, Channel] = {}
        self.policies_end_last = policies_end_last
        self.min_height_new_channels = min_height_new_channels
        self._ref_feerate: int | None = None
        self.feerate_new_local = feerate_new_local
        self.feerate_new_remote = feerate_new_remote
        self.private_only = True
        self._new_channels: list[int] = []

    def add_channel(
        self,
        channel: Channel,
    ) -> None:
        self.channels[channel.chan_id] = channel
        self._pid_channels: list[Channel] | None = None

        if not channel.private:
            self.private_only = False

        if not channel.policy_local:
            return None

        policy_last = self._policy_last(channel.chan_id)
        if not policy_last and channel.opening_height >= self.min_height_new_channels:
            self._new_channels.append(channel.chan_id)
            return None

        # __ref_rate keeps unchanged until here.

        if (
            not self._ref_feerate
            or channel.policy_local.feerate_ppm < self._ref_feerate
        ):
            self._ref_feerate = channel.policy_local.feerate_ppm

    @property
    def liquidity(self) -> tuple[float, float]:
        liquidity_out = sum(
            channel.liquidity_out_pending_sat + channel.liquidity_out_settled_sat
            for channel in self.pid_channels()
        )

        liquidity_in = sum(
            channel.liquidity_in_pending_sat + channel.liquidity_in_settled_sat
            for channel in self.pid_channels()
        )

        return liquidity_out, liquidity_in

    @property
    def ref_feerate(self) -> int:
        if self._ref_feerate is not None:
            return self._ref_feerate

        liquidity_out, liquidity_in = self.liquidity

        if liquidity_out > liquidity_in:
            self._ref_feerate = self.feerate_new_local
        else:
            self._ref_feerate = self.feerate_new_remote

        return self._ref_feerate

    @property
    def ref_feerate_last(self) -> int | None:
        """Returns the feerate_ppm for this collection after the last run of the
        pid controller.

        Returns:
            int | None:
        """

        if not self.policies_end_last:
            return None

        return list(self.policies_end_last.values())[0].feerate_ppm

    @property
    def ref_feerate_changed(self) -> bool:
        ref = self.ref_feerate_last
        if not ref:
            return True
        return ref != self.ref_feerate

    def pid_channels(self) -> Generator[Channel, None, None]:
        if self.private_only:
            return None

        if not self._pid_channels:
            self._pid_channels = self._determine_pid_channels()

        for channel in self._pid_channels:
            yield channel

    def _determine_pid_channels(self) -> list[Channel]:
        """
        Determine all channels which have to be modelled.
        We are returning None if there are only private channel in this
        collection.
        Public channels without any policy at the moment are not returned too,
        which can be the case if a channel was opened lately.
        Otherwise the feerate of the channel has to be equal to the ref_feerate,
        which is the minimum feerate of all feerates.

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

            if channel.policy_local.feerate_ppm == self._ref_feerate:
                pid_channels.append(channel)
                continue

        return pid_channels

    def _policy_last(self, chan_id: int) -> ChannelPolicy | None:
        if not self.policies_end_last:
            return None
        return self.policies_end_last.get(chan_id)


class ChannelAggregator:
    def __init__(
        self,
        config: PidConfig,
    ) -> None:
        self.config = config
        self.channel_collections: dict[str, ChannelCollection] = {}

        self._avg_feerate_local: float | None = None
        self._avg_feerate_target: float | None = None
        self._target_default: float | None = None

    @classmethod
    def from_channels(
        cls,
        config: PidConfig,
        policies_end_last: dict[str, dict[int, ChannelPolicy]],
        block_height: int,
        channels: Iterable[Channel],
    ) -> ChannelAggregator:
        aggregator = cls(config)
        min_height_new_channel = block_height - config.max_age_new_channels
        for channel in channels:
            aggregator.add_channel(
                channel=channel,
                policies_end_last=policies_end_last.get(channel.pub_key),
                min_height_new_channels=min_height_new_channel,
            )

        return aggregator

    def add_channel(
        self,
        channel: Channel,
        policies_end_last: dict[int, ChannelPolicy] | None,
        min_height_new_channels: int,
    ) -> None:
        self._avg_feerate_local = None
        self._avg_feerate_target = None
        self._target_default = None

        pub_key = channel.pub_key

        if channel.pub_key in self.config.exclude_pubkeys:
            return None

        if channel.chan_id in self.config.exclude_chanids:
            return None

        if not (col := self.channel_collections.get(pub_key)):
            peer_config = self.config.peer_config(pub_key)

            col = self.channel_collections[pub_key] = ChannelCollection(
                policies_end_last=policies_end_last,
                min_height_new_channels=min_height_new_channels,
                feerate_new_local=peer_config.feerate_new_local,
                feerate_new_remote=peer_config.feerate_new_remote,
            )

        col.add_channel(channel=channel)

    def pid_channels(self) -> Generator[tuple[Channel, int], None, None]:
        for col in self.channel_collections.values():
            for channel in col.pid_channels():
                yield channel, col.ref_feerate

    def pid_collections(self) -> Generator[tuple[str, ChannelCollection], None, None]:
        for pub_key, collection in self.channel_collections.items():
            if collection.private_only:
                continue
            yield pub_key, collection

    @property
    def avg_feerate_target(self) -> float:
        if not (res := self._avg_feerate_target):
            res = self._calc_avg_feerates()[0]

        return res

    @property
    def avg_feerate_local(self) -> float:
        if not (res := self._avg_feerate_local):
            res = self._calc_avg_feerates()[1]

        return res

    @property
    def target_default(self) -> float:
        if not (res := self._target_default):
            res = self._calc_avg_feerates()[2]

        return res

    def _calc_avg_feerates(self) -> tuple[float, float, float]:
        sum_local: int = 0
        sum_local_known_target: int = 0
        sum_local_feerate: int = 0

        sum_liquidity: int = 0
        sum_liquidity_known_target: int = 0
        sum_liquidity_target: int = 0
        sum_liquidity_feerate: int = 0
        sum_liquidity_feerate_known_target: int = 0
        sum_liquidity_feerate_target: int = 0

        for channel, ref_feerate in self.pid_channels():
            local = (
                channel.liquidity_out_pending_sat + channel.liquidity_out_settled_sat
            )
            remote = channel.liquidity_in_pending_sat + channel.liquidity_in_settled_sat
            sum_local += local
            sum_local_feerate += local * ref_feerate

            sum_liquidity += local + remote
            sum_liquidity_feerate += (local + remote) * ref_feerate

            if (peer_config := self.config.peers.get(channel.pub_key)) and (
                peer_config.target
            ):
                sum_local_known_target += local
                sum_liquidity_known_target += local + remote
                sum_liquidity_feerate_known_target += (local + remote) * ref_feerate

                sum_liquidity_target += (local + remote) * peer_config.target
                sum_liquidity_feerate_target += (
                    (local + remote) * ref_feerate * peer_config.target
                )

        try:
            self._target_default = (
                PEER_TARGET_UNIT
                * (
                    (sum_liquidity - sum_local)
                    - (sum_liquidity_known_target - sum_local_known_target)
                )
                / (sum_liquidity - sum_liquidity_known_target)
            )

        except ZeroDivisionError:
            self._target_default = TARGET_DEFAULT

        try:
            self._avg_feerate_local = sum_local_feerate / sum_local
        except ZeroDivisionError:
            self._avg_feerate_local = 0

        try:
            self._avg_feerate_target = (
                (sum_liquidity_feerate - sum_liquidity_feerate_known_target)
                * self._target_default
                + sum_liquidity_feerate_target
            ) / (
                (sum_liquidity - sum_liquidity_known_target) * self._target_default
                + sum_liquidity_target
            )
        except ZeroDivisionError:
            self._avg_feerate_target = 0

        return (
            self._avg_feerate_target,
            self._avg_feerate_local,
            self._target_default,
        )
