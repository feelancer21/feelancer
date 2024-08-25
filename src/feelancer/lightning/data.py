from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from feelancer.data.db import SessionExecutor
from feelancer.lightning.client import ChannelPolicy, LightningClient
from feelancer.lightning.models import (
    DBLnChannelLiquidity,
    DBLnChannelPeer,
    DBLnChannelPolicy,
    DBLnChannelStatic,
    DBLnNode,
    DBLnRun,
)

if TYPE_CHECKING:
    from sqlalchemy import Select
    from sqlalchemy.orm import Session

    from feelancer.data.db import FeelancerDB
    from feelancer.lightning.client import Channel
    from feelancer.tasks.models import DBRun


ChannelIDX = tuple[int, int]
PolicyIDX = tuple[int, bool]


def _convert_channel_policy(policy: DBLnChannelPolicy) -> ChannelPolicy:
    return ChannelPolicy(
        fee_rate_ppm=policy.fee_rate_ppm,
        base_fee_msat=policy.base_fee_msat,
        time_lock_delta=policy.time_lock_delta,
        min_htlc_msat=policy.min_htlc_msat,
        max_htlc_msat=policy.max_htlc_msat,
        inbound_fee_rate_ppm=policy.inbound_fee_rate_ppm,
        inbound_base_fee_msat=policy.inbound_base_fee_msat,
        disabled=policy.disabled,
        last_update=policy.last_update,
    )


def _new_ln_run(db_run: DBRun, ln_node: DBLnNode) -> DBLnRun:
    return DBLnRun(run=db_run, ln_node=ln_node)


def _new_channel_policy(
    policy: ChannelPolicy,
    channel_static: DBLnChannelStatic,
    ln_run: DBLnRun,
    sequence_id: int,
    local: bool,
) -> DBLnChannelPolicy:
    return DBLnChannelPolicy(
        static=channel_static,
        ln_run=ln_run,
        sequence_id=sequence_id,
        local=local,
        fee_rate_ppm=policy.fee_rate_ppm,
        base_fee_msat=policy.base_fee_msat,
        time_lock_delta=policy.time_lock_delta,
        min_htlc_msat=policy.min_htlc_msat,
        max_htlc_msat=policy.max_htlc_msat,
        inbound_fee_rate_ppm=policy.inbound_fee_rate_ppm,
        inbound_base_fee_msat=policy.inbound_base_fee_msat,
        disabled=policy.disabled,
        last_update=policy.last_update,
    )


def _new_channel_static(
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


def _new_channel_liquidity(
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


def _new_ln_node(pub_key: str) -> DBLnNode:
    return DBLnNode(pub_key=pub_key)


def query_node(pub_key: str) -> Select[tuple[DBLnNode]]:
    return select(DBLnNode).filter_by(pub_key=pub_key)


def query_channel_peers() -> Select[tuple[DBLnChannelPeer]]:
    return select(DBLnChannelPeer)


def query_channel_static(node_id: int) -> Select[tuple[DBLnChannelStatic]]:
    qry = (
        select(DBLnChannelStatic)
        .options(joinedload(DBLnChannelStatic.peer))
        .where(DBLnChannelStatic.ln_node_id == node_id)
    )
    return qry


def query_local_policies(
    run_id: int | None = None, sequence_id: int | None = None
) -> Select[tuple[DBLnChannelPolicy]]:
    """
    Returns a for selecting the local policies out of DBLnChannelPolicy
    """

    qry = (
        select(DBLnChannelPolicy)
        .options(joinedload(DBLnChannelPolicy.static, DBLnChannelStatic.peer))
        .join(DBLnChannelPolicy.ln_run)
        .where(DBLnChannelPolicy.local)
    )

    if run_id:
        qry = qry.where(DBLnChannelPolicy.run_id == run_id)

    if sequence_id:
        qry = qry.where(DBLnChannelPolicy.sequence_id == sequence_id)

    return qry


class LightningStore:
    """
    LightningStore is the interface for all lightning relevant data from the database.
    The methods return non ORM objects only.
    """

    def __init__(self, db: FeelancerDB, pubkey_local: str) -> None:
        self.db = db
        self.pubkey_local = pubkey_local

    def local_policies(self, run_id: int, sequence_id: int) -> dict[int, ChannelPolicy]:

        qry = query_local_policies(run_id=run_id, sequence_id=sequence_id)

        def key(p: DBLnChannelPolicy) -> int:
            return p.static.chan_id

        return self.db.query_all_to_dict(qry, key, _convert_channel_policy)


class LightningSessionCache:
    """
    Caching lightning data from the db and the lightning client during a session
    of sqlalchemy when creating a new run.
    """

    def __init__(self, ln: LightningCache, session: Session, db_run: DBRun) -> None:
        self.db_session = session
        self.exec = SessionExecutor(session)
        self.ln = ln
        self.db_run = db_run
        self.ln_node = self._local_node()
        self.ln_run = _new_ln_run(self.db_run, self.ln_node)

        self._channel_liquidity: dict[ChannelIDX, DBLnChannelLiquidity] | None = None
        self._channel_peer: dict[str, DBLnChannelPeer] | None = None
        self._channel_static: dict[ChannelIDX, DBLnChannelStatic] | None = None
        self._channel_policies: dict[PolicyIDX, dict[ChannelIDX, DBLnChannelPolicy]] = (
            {}
        )

    @property
    def channel_peer(self) -> dict[str, DBLnChannelPeer]:
        if self._channel_peer:
            return self._channel_peer

        """
        Selecting all DBLnChannelPeer from the database and transforming
        into a dict with pub_key as key.
        """
        self._channel_peer = self.exec.query_all_to_dict(
            query_channel_peers(), lambda c: c.pub_key, lambda c: c
        )

        """ Creating DBLnChannelPeer for new peers."""
        for channel in self.ln.channels.values():
            if self._channel_peer.get(pub_key := channel.pub_key):
                continue
            self._channel_peer[pub_key] = DBLnChannelPeer(pub_key=pub_key)

        return self._channel_peer

    @property
    def channel_static(self) -> dict[ChannelIDX, DBLnChannelStatic]:
        if self._channel_static:
            return self._channel_static

        """
        We set channel_static to an empty dict if we have an existing node id,
        we fetch all data from db. If it is the first run for this node, we
        work with the empty dic
        """
        self._channel_static = {}
        if self.ln_node.id:
            qry = query_channel_static(self.ln_node.id)

            # We transform the result to a dict with (node_id, chan_id) as key.
            self._channel_static = self.exec.query_all_to_dict(
                qry, lambda c: (c.ln_node_id, c.chan_id), lambda c: c
            )

        """"Creating DBLnChannelStatic for new channels """
        for channel in self.ln.channels.values():
            idx = self._create_chan_idx(channel)

            if not (self._channel_static.get(idx)):
                self._channel_static[idx] = _new_channel_static(
                    channel, self.channel_peer_by(channel.pub_key), self.ln_node
                )

        return self._channel_static

    @property
    def channel_liquidity(self) -> dict[ChannelIDX, DBLnChannelLiquidity]:
        if self._channel_liquidity:
            return self._channel_liquidity

        self._channel_liquidity = {}
        for channel in self.ln.channels.values():
            idx = self._create_chan_idx(channel)

            self._channel_liquidity[idx] = _new_channel_liquidity(
                channel, self.channel_static_by(channel), self.ln_run
            )

        return self._channel_liquidity

    def channel_policies(
        self, sequence_id: int, local: bool
    ) -> dict[ChannelIDX, DBLnChannelPolicy]:
        """
        Returns the channel policies for a sequence id and one side (local vs remote)
        of the the channel. If no policies exist for this tuple it will be
        created.
        """
        pol_idx = (sequence_id, local)

        if policies := self._channel_policies.get(pol_idx):
            return policies

        policies = self._channel_policies[pol_idx] = {}
        for channel in self.ln.channels_by_sequence(sequence_id).values():
            idx = self._create_chan_idx(channel)
            static = self.channel_static_by(channel)
            if local:
                policy = channel.policy_local
            else:
                policy = channel.policy_remote
            if not policy:
                continue

            policies[idx] = _new_channel_policy(
                policy, static, self.ln_run, sequence_id, local
            )

        return policies

    def channel_peer_by(self, pub_key: str) -> DBLnChannelPeer:
        """Returns DBLnChannelPeer for a given pub_key"""
        return self.channel_peer[pub_key]

    def channel_static_by(self, channel: Channel) -> DBLnChannelStatic:
        """Returns DBLnChannelStatic for a given channel"""
        return self.channel_static[self._create_chan_idx(channel)]

    def _local_node(self) -> DBLnNode:
        pub_key = self.ln.pubkey_local

        return self.exec.query_first(
            query_node(pub_key), lambda c: c, _new_ln_node(pub_key)
        )

    def _create_chan_idx(self, channel: Channel) -> ChannelIDX:
        return (self.ln_node.id, channel.chan_id)


class LightningCache:
    """
    Caching data from a LightningClient which may be used in multiple tasks.
    """

    def __init__(self, lnclient: LightningClient) -> None:
        self.lnclient = lnclient
        self._channels: dict[int, dict[int, Channel]] = {}
        self.pubkey_local: str = self.lnclient.pubkey_local

    @property
    def channels(self) -> dict[int, Channel]:
        return self.channels_by_sequence(sequence_id=0)

    def channels_by_sequence(self, sequence_id: int) -> dict[int, Channel]:
        if not self._channels.get(sequence_id):
            self._channels[sequence_id] = self.lnclient.channels

        return self._channels[sequence_id]
