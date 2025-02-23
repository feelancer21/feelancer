from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import feelancer.lnd.client as lnd
from feelancer.lightning.client import Channel, ChannelPolicy
from feelancer.lightning.utils import opening_height

if TYPE_CHECKING:
    from feelancer.lnd.grpc_generated import lightning_pb2 as ln


def _liquidity_pending(channel: ln.Channel) -> tuple[int, int]:
    pending_out = sum(h.amount for h in channel.pending_htlcs if not h.incoming)
    pending_in = sum(h.amount for h in channel.pending_htlcs if h.incoming)

    return pending_out, pending_in


def _convert_policy(policy: ln.RoutingPolicy | None) -> ChannelPolicy | None:
    if policy:
        return ChannelPolicy(
            fee_rate_ppm=policy.fee_rate_milli_msat,
            base_fee_msat=policy.fee_base_msat,
            time_lock_delta=policy.time_lock_delta,
            disabled=policy.disabled,
            min_htlc_msat=policy.min_htlc,
            max_htlc_msat=policy.max_htlc_msat,
            inbound_fee_rate_ppm=policy.inbound_fee_rate_milli_msat,
            inbound_base_fee_msat=policy.inbound_fee_base_msat,
            last_update=policy.last_update,
        )
    else:
        return None


def _policies_per_side(
    channel_edge: ln.ChannelEdge, pubkey_local: str
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

    return _convert_policy(local_policy), _convert_policy(remote_policy)


class LNDClient:
    def __init__(self, lnd: lnd.LndGrpc) -> None:
        self.lnd = lnd
        self._pubkey_local = self.lnd.get_info().identity_pubkey

    @property
    def block_height(self) -> int:
        return self.lnd.get_info().block_height

    @property
    def channels(self) -> dict[int, Channel]:
        res = {}

        for channel in self.lnd.list_channels().channels:

            # Skipping all channel with a chan_id which is not compatible
            # to BigInteger.
            # TODO: Remove it when we move to strings as channel_id in the database.
            if channel.chan_id >= 2**63:
                logging.error(
                    f"Skipping {channel.channel_point=} because chan_id is too large"
                )
                continue

            # Remark: Calling get_node_info once would be faster. But then we
            # still need get_chan_info for private channels.
            try:
                p_local, p_remote = self.get_channel_policies(channel.chan_id)
            except lnd.EdgeNotFound as e:
                logging.error(f"Skipping {channel.channel_point=} because of '{e}'")
                continue

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
                policy_local=p_local,
                policy_remote=p_remote,
            )

        return res

    def connect_peer(self, pub_key: str) -> None:

        # node info contains alls addresses for the pub key.
        node: ln.NodeInfo = self.lnd.get_node_info(pub_key)

        # Creating a list where onion addresses are at the end.
        # a.addr.find("onion") returns -1 if it is not an onion
        sorted_addresses: list[ln.NodeAddress] = sorted(
            node.node.addresses, key=lambda a: a.addr.find("onion")
        )

        # Trying all addresses. Returning early if a connection was successful
        # or the peer is already connected.
        for a in sorted_addresses:
            host = a.addr
            try:
                logging.debug(f"Trying to connect: {pub_key=}@{host=}")
                self.lnd.connect_peer(pub_key=pub_key, host=host)
                logging.info(f"Connected to {pub_key=}; {host=}")
                return None

            except lnd.PeerAlreadyConnected as e:
                logging.debug(f"Received from lnd: {e}")
                return None

            except (lnd.DialProxFailed, lnd.EOF) as e:
                logging.error(f"Received from lnd: {e}")

    def disconnect_peer(self, pub_key: str) -> None:

        # Calling the backend to disconnect the peer. We are fine it the peer
        # is already disconnected.
        try:
            self.lnd.disconnect_peer(pub_key)
            logging.info(f"Disonnected from {pub_key=}")
        except lnd.PeerNotConnected as e:
            logging.debug(f"Received from lnd: {e}")

    def get_channel_policies(
        self, chan_id: int
    ) -> tuple[ChannelPolicy | None, ChannelPolicy | None]:
        """
        Returns a tuple consisting of the local and remote ChannelPolicy for
        a given chan_id.
        """

        edge = self.lnd.get_chan_info(chan_id)
        return _policies_per_side(edge, self.pubkey_local)

    @property
    def pubkey_local(self) -> str:
        """
        Returns the pubkey of the node behind this client.
        """

        return self._pubkey_local

    def update_channel_policy(
        self,
        chan_point: str,
        fee_rate_ppm: int,
        base_fee_msat: int,
        time_lock_delta: int,
        inbound_fee_rate_ppm: int,
        inbound_base_fee_msat: int,
    ) -> None:

        response = self.lnd.update_channel_policy(
            chan_point=chan_point,
            fee_rate_ppm=fee_rate_ppm,
            time_lock_delta=time_lock_delta,
            base_fee_msat=base_fee_msat,
            inbound_fee_rate_ppm=inbound_fee_rate_ppm,
            inbound_base_fee_msat=inbound_base_fee_msat,
        )

        if not response:
            return

        for f in response.failed_updates:
            logging.error(
                f"update failure for chan_point {chan_point}; error: "
                + f"{f.update_error}; failure: {lnd.update_failure_name(f.reason)}"
            )
        if len(response.failed_updates) > 0:
            raise Exception("update failure during policy update")

    def start(self) -> None:
        return self.lnd.start()

    def stop(self) -> None:
        return self.lnd.stop()
