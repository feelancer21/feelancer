"""
Defines a Protocol for lightning client. Currently implemented for lnd only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ChannelPolicy:
    fee_rate_ppm: int
    base_fee_msat: int
    time_lock_delta: int
    min_htlc_msat: int
    max_htlc_msat: int
    inbound_fee_rate_ppm: int
    inbound_base_fee_msat: int
    disabled: bool
    last_update: int


@dataclass
class Channel:
    chan_id: int
    chan_point: str
    pub_key: str
    private: bool
    opening_height: int
    capacity_sat: int
    liquidity_out_settled_sat: int
    liquidity_out_pending_sat: int
    liquidity_in_settled_sat: int
    liquidity_in_pending_sat: int
    policy_local: ChannelPolicy | None
    policy_remote: ChannelPolicy | None


class LightningClient(Protocol):
    """
    Serves as a interface for a lightning client which is currently implemented
    by LndClient.
    """

    @property
    def block_height(self) -> int:
        """
        Fetches the current block height from the lightning client.
        """
        ...

    @property
    def channels(self) -> dict[int, Channel]:
        """
        Fetches all channels from the lightning client and creates a dictionary
        of all channels with chan_id as key.
        """
        ...

    @property
    def pubkey_local(self) -> str:
        """
        Returns the pubkey of the local node.
        """
        ...

    def update_channel_policy(
        self,
        chan_point: str,
        fee_rate_ppm: int,
        base_fee_msat: int,
        time_lock_delta: int,
        inbound_fee_rate_ppm: int,
        inbound_base_fee_msat: int,
    ) -> None:
        """
        Updates the channel policy for given chan point.
        """
        ...
