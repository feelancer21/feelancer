from __future__ import annotations

from typing import TYPE_CHECKING

from feelancer.lightning.client import Channel, ChannelPolicy, LightningClient
from feelancer.lightning.utils import opening_height

if TYPE_CHECKING:
    from lndgrpc import LNDClient as LndGrpcClient


def _liquidity_pending(channel) -> tuple[int, int]:
    pending_out = sum(h.amount for h in channel.pending_htlcs if not h.incoming)
    pending_in = sum(h.amount for h in channel.pending_htlcs if h.incoming)

    return pending_out, pending_in


def _cast_policy(policy) -> ChannelPolicy | None:
    if policy:
        return ChannelPolicy(
            feerate_ppm=policy.fee_rate_milli_msat,
            basefee_msat=policy.fee_base_msat,
            timelockdelta=policy.time_lock_delta,
            disabled=policy.disabled,
            min_htlc_msat=policy.min_htlc,
            max_htlc_msat=policy.max_htlc_msat,
            last_update=policy.last_update,
        )
    else:
        return None


def _policies_per_side(
    channel_edge, pubkey_local: str
) -> tuple[ChannelPolicy | None, ChannelPolicy | None]:
    if channel_edge.node1_pub == pubkey_local:
        local_policy, remote_policy = (
            channel_edge.node1_policy,
            channel_edge.node2_policy,
        )
    else:
        local_policy, remote_policy = (
            channel_edge.node2_policy,
            channel_edge.node1_policy,
        )

    return _cast_policy(local_policy), _cast_policy(remote_policy)


class LNDClient(LightningClient):
    def __init__(self, lnd: LndGrpcClient) -> None:
        self.lnd = lnd
        super().__init__()

    @property
    def block_height(self) -> int:
        return self.lnd.get_info().block_height

    @property
    def channels(self) -> dict[int, Channel]:
        res = {}
        policies_local, policies_remote = self._channel_policies()

        for channel in self.lnd.list_channels().channels:
            liq_pending_out, liq_pending_in = _liquidity_pending(channel)
            res[channel.chan_id] = Channel(
                chan_id=channel.chan_id,
                chan_point=channel.channel_point,
                pub_key=channel.remote_pubkey,
                private=channel.private,
                opening_height=opening_height(channel.chan_id),
                capacity_sat=channel.capacity,
                liquidity_out_settled_sat=channel.local_balance,
                liquidity_out_pending_sat=liq_pending_out,
                liquidity_in_settled_sat=channel.remote_balance,
                liquidity_in_pending_sat=liq_pending_in,
                policy_local=policies_local.get(channel.chan_id),
                policy_remote=policies_remote.get(channel.chan_id),
            )

        return res

    def _channel_policies(
        self,
    ) -> tuple[dict[int, ChannelPolicy], dict[int, ChannelPolicy]]:
        return self._get_policies_from_pubkey(self.pubkey_local)

    @property
    def pubkey_local(self) -> str:
        return self.lnd.get_info().identity_pubkey

    def update_channel_policy(
        self, chan_point: str, feerate_ppm: int, basefee_msat: int, time_lock_delta: int
    ) -> None:
        response = self.lnd.update_channel_policy(
            chan_point=chan_point,
            fee_rate=feerate_ppm / 1_000_000,
            time_lock_delta=time_lock_delta,
            base_fee_msat=basefee_msat,
        )

        if not response:
            return

        for f in response.failed_updates:
            print("FAILED_UPDATE:", chan_point, f)

    def _get_policies_from_pubkey(
        self, pubkey: str
    ) -> tuple[dict[int, ChannelPolicy], dict[int, ChannelPolicy]]:
        policies_local: dict = {}
        policies_remote: dict = {}

        node_info = self.lnd.get_node_info(pub_key=pubkey, include_channels=True)

        for channel_edge in node_info.channels:
            local_policy, remote_policy = _policies_per_side(channel_edge, pubkey)
            if local_policy:
                policies_local[channel_edge.channel_id] = local_policy

            if remote_policy:
                policies_remote[channel_edge.channel_id] = remote_policy

        return policies_local, policies_remote
