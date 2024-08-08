from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session, joinedload

from feelancer.lightning.client import ChannelPolicy, LightningClient
from feelancer.lightning.enums import PolicyFetchType
from feelancer.lightning.models import (
    DBLnChannelLiquidity,
    DBLnChannelPeer,
    DBLnChannelPolicy,
    DBLnChannelStatic,
    DBLnNode,
    DBLnRun,
)

if TYPE_CHECKING:
    from feelancer.lightning.client import Channel
    from feelancer.tasks.models import DBRun


ChannelIDX = tuple[int, int]
PolicyIDX = tuple[PolicyFetchType, bool]


def convert_from_channel_policy(policy: DBLnChannelPolicy) -> ChannelPolicy:
    return ChannelPolicy(
        feerate_ppm=policy.feerate_ppm,
        basefee_msat=policy.basefee_msat,
        timelockdelta=policy.timelockdelta,
        min_htlc_msat=policy.min_htlc_msat,
        max_htlc_msat=policy.max_htlc_msat,
        disabled=policy.disabled,
        last_update=policy.last_update,
    )


def _convert_ln_run(db_run: DBRun, ln_node: DBLnNode) -> DBLnRun:
    return DBLnRun(run=db_run, ln_node=ln_node)


def _convert_to_channel_policy(
    policy: ChannelPolicy,
    channel_static: DBLnChannelStatic,
    ln_run: DBLnRun,
    fetch_type: PolicyFetchType,
    local: bool,
) -> DBLnChannelPolicy:
    return DBLnChannelPolicy(
        static=channel_static,
        ln_run=ln_run,
        fetch_type=fetch_type,
        local=local,
        feerate_ppm=policy.feerate_ppm,
        basefee_msat=policy.basefee_msat,
        timelockdelta=policy.timelockdelta,
        min_htlc_msat=policy.min_htlc_msat,
        max_htlc_msat=policy.max_htlc_msat,
        disabled=policy.disabled,
        last_update=policy.last_update,
    )


def _convert_to_channel_static(
    channel: Channel, channel_peer: DBLnChannelPeer, ln_node: DBLnNode
) -> DBLnChannelStatic:
    return DBLnChannelStatic(
        chan_id=channel.chan_id,
        chan_point=channel.chan_point,
        opening_height=channel.opening_height,
        private=channel.private,
        peer=channel_peer,
        capacity=channel.capacity_sat,
        ln_node=ln_node,
    )


def _convert_to_channel_liquidity(
    channel: Channel, channel_static: DBLnChannelStatic, ln_run: DBLnRun
) -> DBLnChannelLiquidity:
    return DBLnChannelLiquidity(
        ln_run=ln_run,
        liquidity_out_settled_sat=channel.liquidity_out_settled_sat,
        liquidity_out_pending_sat=channel.liquidity_out_pending_sat,
        liquidity_in_settled_sat=channel.liquidity_in_settled_sat,
        liquidity_in_pending_sat=channel.liquidity_in_pending_sat,
        static=channel_static,
    )


def get_local_node(session: Session, pub_key: str) -> DBLnNode:
    if not (node := session.query(DBLnNode).filter_by(pub_key=pub_key).first()):
        node = DBLnNode(pub_key=pub_key)
    return node


def _get_channel_peers(session: Session) -> dict[str, DBLnChannelPeer]:
    return {p.pub_key: p for p in session.query(DBLnChannelPeer).all()}


def _get_channels_static(
    session: Session, ln_node: DBLnNode
) -> dict[tuple[int, int], DBLnChannelStatic]:
    if not ln_node.id:
        return {}

    return {
        (c.ln_node_id, c.chan_id): c
        for c in session.query(DBLnChannelStatic)
        .options(joinedload(DBLnChannelStatic.peer))
        .filter_by(ln_node=ln_node)
        .all()
    }


class LightningSessionCache:
    """
    Caching Lightning data from the db and the Lightning Client during a session
    of sqlalchemy
    """

    def __init__(self, ln: LightningCache, session: Session, db_run: DBRun) -> None:
        self.db_session = session
        self.ln = ln

        self.db_run = db_run
        self.ln_node = get_local_node(self.db_session, ln.pubkey_local)
        self.ln_run = _convert_ln_run(self.db_run, self.ln_node)

        self._channel_liquidity: dict[ChannelIDX, DBLnChannelLiquidity] | None = None
        self._channel_peer: dict[str, DBLnChannelPeer] | None = None
        self._channel_static: dict[ChannelIDX, DBLnChannelStatic] | None = None
        self._channel_policies: dict[
            PolicyIDX, dict[ChannelIDX, DBLnChannelPolicy]
        ] = {}

    @property
    def channel_peer(self) -> dict[str, DBLnChannelPeer]:
        if self._channel_peer:
            return self._channel_peer

        self._channel_peer = _get_channel_peers(self.db_session)
        for channel in self.ln.channels.values():
            if self._channel_peer.get(pub_key := channel.pub_key):
                continue
            self._channel_peer[pub_key] = DBLnChannelPeer(pub_key=pub_key)

        return self._channel_peer

    @property
    def channel_static(self) -> dict[ChannelIDX, DBLnChannelStatic]:
        if self._channel_static:
            return self._channel_static

        self._channel_static = _get_channels_static(self.db_session, self.ln_node)
        for channel in self.ln.channels.values():
            idx = self._get_chan_idx(channel)

            if not (self._channel_static.get(idx)):
                self._channel_static[idx] = _convert_to_channel_static(
                    channel, self.get_channel_peer(channel.pub_key), self.ln_node
                )

        return self._channel_static

    @property
    def channel_liquidity(self) -> dict[ChannelIDX, DBLnChannelLiquidity]:
        if self._channel_liquidity:
            return self._channel_liquidity

        self._channel_liquidity = {}
        for channel in self.ln.channels.values():
            idx = self._get_chan_idx(channel)

            self._channel_liquidity[idx] = _convert_to_channel_liquidity(
                channel, self.get_channel_static(channel), self.ln_run
            )

        return self._channel_liquidity

    def set_channel_policies(self, fetch_type: PolicyFetchType, local: bool) -> None:
        pol_idx = (fetch_type, local)

        if self._channel_policies.get(pol_idx):
            return None

        policies = self._channel_policies[pol_idx] = {}
        for channel in self.ln.get_channels(fetch_type).values():
            idx = self._get_chan_idx(channel)
            static = self.get_channel_static(channel)
            if local:
                policy = channel.policy_local
            else:
                policy = channel.policy_remote
            if not policy:
                continue

            policies[idx] = _convert_to_channel_policy(
                policy, static, self.ln_run, fetch_type, local
            )

    def _get_chan_idx(self, channel: Channel) -> ChannelIDX:
        return (self.ln_node.id, channel.chan_id)

    def get_channel_peer(self, pub_key: str) -> DBLnChannelPeer:
        return self.channel_peer[pub_key]

    def get_channel_static(self, channel: Channel) -> DBLnChannelStatic:
        return self.channel_static[self._get_chan_idx(channel)]


class LightningCache:
    """
    Caching data from a LightningClient during a TaskSession
    """

    def __init__(self, lnclient: LightningClient) -> None:
        self.lnclient = lnclient
        self._channels: dict[PolicyFetchType, dict[int, Channel]] = {}
        self.pubkey_local: str = self.lnclient.pubkey_local

    @property
    def channels(self) -> dict[int, Channel]:
        return self.get_channels(PolicyFetchType.START)

    def get_channels(self, fetch_type: PolicyFetchType) -> dict[int, Channel]:
        if not self._channels.get(fetch_type):
            self._channels[fetch_type] = self.lnclient.channels

        return self._channels[fetch_type]
