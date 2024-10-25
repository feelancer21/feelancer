from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
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
    A proposal for a policy update. If an attribute is set to None, then no
    update is processed.
    """

    channel: Channel

    # force_update forces an policy update in the case the last_update with the
    # peer was too recent
    force_update: bool = False

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
    # force_update forces an policy update in the case the last_update with the
    # peer was too recent
    force_update: bool = False
    # Is a update for the outbound policy needed
    outbound_changed: bool = False
    # Is a update for the inbound policy needed
    inbound_changed: bool = False
    # List of the proposals with its effective fee rates considering min/max
    # restrictions
    proposals: list[PolicyProposal] = field(default_factory=list)


def _get_max_min(input: int, max_value: int, min_value: int) -> int:
    return max(min(input, max_value), min_value)


def _is_changed(
    value_new: int,
    value_old: int,
    min_up: int,
    min_down: int,
    min_value: int,
    max_value: int,
) -> bool:
    """
    Compares the new value with the old value and returns True if a positive
    delta is greater equal than min_up and the absolute value of a negative value
    is greater equal than min_down.
    If a change hits the min/max restriction we also return True.
    """

    delta = value_new - value_old

    if -delta > 0 and value_new == min_value:
        return True

    if delta > 0 and value_new == max_value:
        return True

    if delta >= min_up or -delta >= min_down:
        return True
    return False


def _create_update_policy(
    policy: ChannelPolicy, proposal: PolicyProposal, info: PolicyUpdateInfo
) -> ChannelPolicy:
    """
    Merges the information of the existing policy, the proposal and the update
    info together and creates the policy with the data to update.
    """

    # Creates a copy of the current policy with all existing attributes, and
    # replaces the values with the proposal, if something has changed.
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


def _orders_proposals_by_peer(
    proposals: Iterable[PolicyProposal],
) -> dict[str, list[PolicyProposal]]:
    """
    Orders the PolicyProposal and returns a dictionary with pub_key as key and
    a list of all PolicyProposal as items
    """

    res: dict[str, list[PolicyProposal]] = {}

    for r in proposals:
        pub_key = r.channel.pub_key

        if not res.get(pub_key):
            res[pub_key] = []

        res[pub_key].append(r)

    return res


def _check_value_restrictions(
    proposals: Iterable[PolicyProposal],
    peer_config: FeelancerPeersConfig,
) -> PolicyUpdateInfo:
    """
    Checks if the proposed value are aligned with the restrictions in the
    config and returns an PolicyUpdateInfo with the results. It checks the
    min/max restrictions and the min/max delta restrictions.
    """

    info = PolicyUpdateInfo()
    c = peer_config
    for r in proposals:
        if not (policy := r.channel.policy_local):
            continue

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
                c.fee_rate_min,
                c.fee_rate_max,
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
                c.inbound_fee_rate_min,
                c.inbound_fee_rate_max,
            )

        # Creating a new proposal considering the fee rates with min/max
        # restrictions.
        info.proposals.append(
            PolicyProposal(
                channel=r.channel,
                fee_rate_ppm=fee_rate,
                inbound_fee_rate_ppm=inbound_fee_rate,
            )
        )

        # Updating the info. The booleans are concatenated with an OR operation,
        # i.e. it is sufficient that one channel with this peer requires an update
        info.max_last_update = max(info.max_last_update, policy.last_update)
        info.force_update |= r.force_update
        info.outbound_changed |= fee_rate_changed
        info.inbound_changed |= inbound_fee_rate_changed

    return info


def _create_update_policies(
    proposals: Iterable[PolicyProposal],
    pub_key: str,
    peer_config: FeelancerPeersConfig,
    timenow: datetime,
) -> dict[str, ChannelPolicy]:
    """
    Creates a dict of ChannelPolicy's. Peers are skipped, when the last policy
    update was too recent, or no update is needed because the inbound or outbound
    fees haven't changed significantly.
    """

    final_policies: dict[str, ChannelPolicy] = {}

    # Check of min/max restrictions.
    info = _check_value_restrictions(proposals, peer_config)

    # We return with an empty dict if the last update was too recent and if
    # we don't want to force the update.
    if (not info.force_update) and (
        dt := timenow.timestamp() - info.max_last_update
    ) < peer_config.min_seconds:

        logging.debug(
            f"no policy updates for {pub_key=}; last update was {dt}s ago "
            f"which is less than min_seconds {peer_config.min_seconds}s."
        )
        return final_policies

    # If values haven't changed significantly we can skip the all channels,
    if not (info.outbound_changed or info.inbound_changed):
        logging.debug(f"no policy update for {pub_key=} needed.")
        return final_policies

    # Looping over all proposals now and creating the final policy which
    for r in info.proposals:
        p = r.channel.policy_local
        if not p:
            continue

        final_policies[r.channel.chan_point] = _create_update_policy(p, r, info)

    return final_policies


def _update_channel_policies_peer(
    ln: LightningClient,
    proposals: Iterable[PolicyProposal],
    pub_key: str,
    peer_config: FeelancerPeersConfig,
    timenow: datetime,
) -> None:
    """
    Checks if each policy proposal is aligned with update restrictions in the
    config for a specfifc peer.
    In the second step the lightning backend is updated with the new policies.
    """

    update_policies = _create_update_policies(proposals, pub_key, peer_config, timenow)

    # Updating the final policies
    for chan_point, policy in update_policies.items():
        # common part of log message
        msg = (
            f"for {chan_point=}; {policy.fee_rate_ppm=}; "
            f"{policy.inbound_fee_rate_ppm=}"
        )

        try:
            ln.update_channel_policy(
                chan_point,
                policy.fee_rate_ppm,
                policy.base_fee_msat,
                policy.time_lock_delta,
                policy.inbound_fee_rate_ppm,
                policy.inbound_base_fee_msat,
            )

            logging.info(f"policy update successful {msg}")
        except Exception as e:
            # RpcErrors are absorbed here too.
            logging.error(f"policy update failed {msg}; error {e}")

    return None


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

    props_by_peer = _orders_proposals_by_peer(proposals)

    for pub_key, props in props_by_peer.items():
        config = get_peer_config(pub_key)
        _update_channel_policies_peer(ln, props, pub_key, config, timenow)
