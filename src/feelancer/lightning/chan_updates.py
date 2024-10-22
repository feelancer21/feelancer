from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Iterable
from feelancer.utils import first_some

from .client import ChannelPolicy

if TYPE_CHECKING:
    from feelancer.config import FeelancerPeersConfig

    from .client import Channel, LightningClient


@dataclass
class PolicyProposal:
    """
    A proposal for a policy update.
    """

    channel: Channel
    fee_rate_ppm: int | None = None
    base_fee_msat: int | None = None
    time_lock_delta: int | None = None
    min_htlc_msat: int | None = None
    max_htlc_msat: int | None = None
    inbound_fee_rate_ppm: int | None = None
    inbound_base_fee_msat: int | None = None
    disabled: bool | None = None


@dataclass
class PolicyUpdateInfo:
    """
    Used to store aggregated policy and proposal information at the individual
    peer level.
    We don't want to spam the network with tiny policy dates in short time
    ranges. Therefore store the maximum of the last policy update timestamp
    for all channels. Moreover we have to distinguish whether we want update
    the outbound policy and/or the inbound policy, because we want to make the
    channel with a update on side not unusable on the other side.
    """

    # The last update timestamp over all channels
    max_last_update: int = 0
    # Is a update for the outbound policy needed
    outbound_changed: bool = False
    # Is a update for the inbound policy needed
    inbound_changed: bool = False


def _get_max_min(input: int, max_value: int, min_value: int) -> int:
    return max(min(input, max_value), min_value)


def _is_changed(value_current: int, value_old: int, min_up: int, min_down: int) -> bool:
    delta = value_current - value_old
    if delta >= min_up or -delta >= min_down:
        return True
    return False


def _create_update_policy(
    policy: ChannelPolicy, proposal: PolicyProposal, info: PolicyUpdateInfo
) -> ChannelPolicy:
    """
    Merges the information of the existing policy, the proposal and the update
    info together and creates a final policy
    """

    res = copy.copy(policy)
    if info.outbound_changed:
        res.fee_rate_ppm = first_some(proposal.fee_rate_ppm, res.inbound_fee_rate_ppm)
        res.base_fee_msat = first_some(proposal.base_fee_msat, res.base_fee_msat)
        res.time_lock_delta = first_some(proposal.time_lock_delta, res.time_lock_delta)
        res.max_htlc_msat = first_some(proposal.max_htlc_msat, res.max_htlc_msat)
        res.min_htlc_msat = first_some(proposal.min_htlc_msat, res.min_htlc_msat)
        res.disabled = first_some(proposal.disabled, res.disabled)

    if info.inbound_changed:
        res.inbound_fee_rate_ppm = first_some(
            proposal.inbound_fee_rate_ppm, res.inbound_fee_rate_ppm
        )
        res.inbound_base_fee_msat = first_some(
            proposal.inbound_base_fee_msat, res.inbound_base_fee_msat
        )

    return res


def update_channel_policies(
    ln: LightningClient,
    proposals: Iterable[PolicyProposal],
    get_peer_config: Callable[[str], FeelancerPeersConfig],
    timenow: datetime,
) -> None:
    """
    Checks if each policy proposal is aligned with update restrictions in the
    config. In the second step the lightning backend is updated with the new
    policies.
    """

    # dict pub_key -> chan_point -> PolicyProposal
    prop_dict: dict[str, dict[str, PolicyProposal]] = {}

    # dict pub_key -> PolicyUpdateInfo
    info_dict: dict[str, PolicyUpdateInfo] = {}

    for r in proposals:
        if not (policy := r.channel.policy_local):
            continue

        pub_key = r.channel.pub_key
        chan_point = r.channel.chan_point
        c = get_peer_config(pub_key)

        # We check for outbound and inbound fee rate whether there is a min or
        # max in the config. Moreover we check whether the delta to the current
        # value is large enough

        fee_rate = None
        fee_rate_changed = False
        if r.fee_rate_ppm is not None:
            fee_rate = _get_max_min(
                r.fee_rate_ppm, c.fee_rate_max, max(c.fee_rate_min, 0)
            )
            fee_rate_changed = _is_changed(
                fee_rate,
                policy.fee_rate_ppm,
                c.fee_rate_ppm_min_up,
                c.fee_rate_ppm_min_down,
            )

        inbound_fee_rate = None
        inbound_fee_rate_changed = False
        if r.inbound_fee_rate_ppm is not None:
            inbound_fee_rate = _get_max_min(
                r.inbound_fee_rate_ppm, c.inbound_fee_rate_max, c.inbound_fee_rate_min
            )
            inbound_fee_rate_changed = _is_changed(
                inbound_fee_rate,
                policy.inbound_fee_rate_ppm,
                c.inbound_fee_rate_ppm_min_up,
                c.inbound_fee_rate_ppm_min_down,
            )

        # If one of multiple channels with a peer needs a policy update, we want
        # to update all channels, to avoid different fee rates between them.
        # That's why we store all policy proposals in dict.
        if not prop_dict.get(pub_key):
            prop_dict[pub_key] = {}

        prop_dict[pub_key][chan_point] = PolicyProposal(
            channel=r.channel,
            fee_rate_ppm=fee_rate,
            inbound_fee_rate_ppm=inbound_fee_rate,
        )

        if not (info := info_dict.get(pub_key)):
            info = info_dict[pub_key] = PolicyUpdateInfo()

        # Updating the info. The booleans are concatenated with an OR operation,
        # i.e. it is sufficient that one channel with this peer requires an update
        info.max_last_update = max(info.max_last_update, policy.last_update)
        info.outbound_changed |= fee_rate_changed
        info.inbound_changed |= inbound_fee_rate_changed

    # Iterating over all peers with a max last update. If the time delta fits
    # with the config we update all channels with this peer.
    for pub_key, info in info_dict.items():
        peer_config = get_peer_config(pub_key)
        if (dt := timenow.timestamp() - info.max_last_update) < peer_config.min_seconds:
            logging.debug(
                f"no policy updates for {pub_key}; last update was {dt}s ago "
                f"which is less than min_seconds {peer_config.min_seconds}s."
            )
            continue

        update = prop_dict.get(pub_key)
        if not update:
            continue

        # Check on peer level whether an update is needed. If not we skip all
        # channels.
        if not (info.outbound_changed or info.inbound_changed):
            logging.debug(f"no policy update for {pub_key} needed.")
            continue

        # Looping over all channels with this peer now.
        for chan_point, r in update.items():
            p = r.channel.policy_local
            if not p:
                continue

            final = _create_update_policy(p, r, info)

            # common part of log message
            msg = (
                f"for chan_point: {chan_point}; fee_rate_ppm: {final.fee_rate_ppm}; "
                f"inbound_fee_rate_ppm: {final.inbound_fee_rate_ppm}"
            )

            try:
                ln.update_channel_policy(
                    chan_point,
                    final.fee_rate_ppm,
                    final.base_fee_msat,
                    final.time_lock_delta,
                    final.inbound_fee_rate_ppm,
                    final.inbound_base_fee_msat,
                )

                logging.info(f"policy update successful {msg}")
            except Exception as e:
                # RpcErrors are absorbed here too.
                logging.error(f"policy update failed {msg}; error {e}")

    return None
