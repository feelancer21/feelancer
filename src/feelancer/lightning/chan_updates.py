from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from feelancer.config import FeelancerConfig

    from .client import Channel, LightningClient


@dataclass
class PolicyProposal:
    channel: Channel
    fee_rate_ppm: int | None = None
    base_fee_msat: int | None = None
    time_lock_delta: int | None = None
    min_htlc_msat: int | None = None
    max_htlc_msat: int | None = None
    inbound_fee_rate_ppm: int | None = None
    inbound_base_fee_msat: int | None = None
    disabled: bool | None = None


def _get_max_min(input: int, max_value: int, min_value: int) -> int:
    return max(min(input, max_value), min_value)


def _is_changed(value_current: int, value_old: int, min_up: int, min_down: int) -> bool:

    delta = value_current - value_old
    if delta >= min_up or -delta >= min_down:
        return True
    return False


def update_channel_policies(
    ln: LightningClient,
    proposals: Iterable[PolicyProposal],
    config: FeelancerConfig,
    timenow: datetime,
) -> None:
    # map: pup_key -> timestamp
    max_last_update: dict[str, int] = {}
    # map: pub_key -> chan_point -> PolicyProposal
    updates: dict[str, dict[str, PolicyProposal]] = {}

    for r in proposals:
        if not (policy := r.channel.policy_local):
            continue

        pub_key = r.channel.pub_key
        chan_point = r.channel.chan_point
        c = config.peer_config(pub_key)

        fee_rate = None
        fee_rate_changed = False
        if r.fee_rate_ppm:
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
        if r.inbound_fee_rate_ppm:
            inbound_fee_rate = _get_max_min(
                r.inbound_fee_rate_ppm, c.inbound_fee_rate_max, c.inbound_fee_rate_min
            )
            inbound_fee_rate_changed = _is_changed(
                inbound_fee_rate,
                policy.inbound_fee_rate_ppm,
                c.inbound_fee_rate_ppm_min_up,
                c.inbound_fee_rate_ppm_min_down,
            )

        if not any([fee_rate_changed, inbound_fee_rate_changed]):
            logging.debug(
                f"no policy update for {chan_point} because the values only "
                f"changed slightly."
            )
            continue

        if not (m := max_last_update.get(pub_key)):
            max_last_update[pub_key] = policy.last_update
        else:
            max_last_update[pub_key] = max(m, policy.last_update)

        if not updates.get(pub_key):
            updates[pub_key] = {}

        updates[pub_key][chan_point] = PolicyProposal(
            channel=r.channel,
            fee_rate_ppm=fee_rate,
            inbound_fee_rate_ppm=inbound_fee_rate,
        )

    for pub_key, timestamp in max_last_update.items():
        peer_config = config.peer_config(pub_key)
        if (dt := timenow.timestamp() - timestamp) < peer_config.min_seconds:
            logging.debug(
                f"no policy updates for {pub_key}; last update was {dt}s ago "
                f"which is less than min_seconds {peer_config.min_seconds}s."
            )
            continue

        if not (update := updates.get(pub_key)):
            continue

        for chan_point, r in update.items():
            p = r.channel.policy_local
            if not p:
                continue

            fee_rate = r.fee_rate_ppm or p.fee_rate_ppm
            inbound_fee_rate = r.inbound_fee_rate_ppm or p.inbound_fee_rate_ppm

            # common part of log message
            msg = (
                f"for chan_point: {chan_point}; fee_rate_ppm: {fee_rate}; "
                f"inbound_fee_rate_ppm: {inbound_fee_rate}"
            )

            try:
                ln.update_channel_policy(
                    chan_point,
                    fee_rate,
                    p.base_fee_msat,
                    p.time_lock_delta,
                    inbound_fee_rate,
                    p.inbound_base_fee_msat,
                )

                logging.info(f"policy update successful {msg}")
            except Exception as e:
                logging.error(f"policy update failed {msg}; error {e}")

    return None
