from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChannelPolicy:
    feerate_ppm: int
    basefee_msat: int
    timelockdelta: int
    min_htlc_msat: int
    max_htlc_msat: int
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


class LightningClient(ABC):
    @property
    @abstractmethod
    def block_height(self) -> int:
        """
        Fetches the current block height from the lightning client.
        """
        pass

    @property
    @abstractmethod
    def channels(self) -> dict[int, Channel]:
        """
        Fetches all channels from lightning client and creates a dictionary
        of all channels with chan_id as key.
        """
        pass

    @property
    @abstractmethod
    def pubkey_local(self) -> str:
        """
        Fetches the pubkey of the local node.
        """
        pass

    @abstractmethod
    def update_channel_policy(
        self, chan_point: str, feerate_ppm: int, basefee_msat: int, time_lock_delta: int
    ) -> None:
        pass
