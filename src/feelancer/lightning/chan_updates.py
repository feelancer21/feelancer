from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from feelancer.config import FeelancerConfig

    from .client import Channel, ChannelPolicy, LightningClient


@dataclass
class PolicyRecommendation:
    channel: Channel
    feerate_ppm: float | None = None
    basefee_msat: float | None = None
    timelockdelta: float | None = None
    min_htlc_msat: float | None = None
    max_htlc_msat: float | None = None
    disabled: bool | None = None


def update_channel_policies(
    client: LightningClient,
    recommendations: Iterable[PolicyRecommendation],
    config: FeelancerConfig,
) -> None:
    # map: pup_key -> timestamp
    max_last_update: dict[str, int] = {}
    # map: pub_key -> chan_point -> feerate
    feerate_update: dict[str, dict[str, int]] = {}
    policies: dict[str, ChannelPolicy] = {}

    timenow = datetime.now().timestamp()

    for r in recommendations:
        if r.channel.private:
            continue

        if not r.feerate_ppm:
            continue

        peer_config = config.peer_config(r.channel.pub_key)
        feerate = int(r.feerate_ppm)
        if feerate > peer_config.feerate_max:
            feerate = peer_config.feerate_max
        if feerate < max(peer_config.feerate_min, 0):
            feerate = max(peer_config.feerate_min, 0)

        if not (policy := r.channel.policy_local):
            continue
        elif (
            max(feerate - policy.feerate_ppm, 0) < peer_config.feerate_min_ppm_up
            and max(policy.feerate_ppm - feerate, 0) < peer_config.feerate_min_ppm_down
        ):
            continue
        elif not max_last_update.get(r.channel.pub_key):
            max_last_update[r.channel.pub_key] = policy.last_update
        else:
            max_last_update[r.channel.pub_key] = max(
                max_last_update[r.channel.pub_key], policy.last_update
            )

        if not feerate_update.get(r.channel.pub_key):
            feerate_update[r.channel.pub_key] = {}

        policies[r.channel.chan_point] = policy
        feerate_update[r.channel.pub_key][r.channel.chan_point] = feerate

    for pub_key, timestamp in max_last_update.items():
        peer_config = config.peer_config(pub_key)
        if timenow - timestamp < peer_config.min_seconds:
            continue

        if not (update := feerate_update.get(pub_key)):
            continue

        for chan_point, feerate in update.items():
            p = policies[chan_point]
            client.update_channel_policy(
                chan_point, feerate, p.basefee_msat, p.timelockdelta
            )

    return None
