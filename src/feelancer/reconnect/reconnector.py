import logging
import time
from typing import TYPE_CHECKING, Protocol

from feelancer.lightning.lnd import LNDClient

if TYPE_CHECKING:
    from feelancer.lnd.grpc_generated import lightning_pb2 as ln

logger = logging.getLogger(__name__)


class Reconnector(Protocol):
    """
    Protocol with methods for reconnecting channels.
    """

    def reconnect_channels(
        self, max_blocks_to_expiry: int, include_inactive: bool
    ) -> None:
        """
        Disconnects and connects all channels part of stuck htlcs, i.e.
        the incoming and the outgoing channels. A stuck htlc is an htlc with
        an expiry less than the specified number of blocks.
        Optionally you can also reconnect inactive channels.
        """
        ...


class LNDReconnector(LNDClient):
    """
    Implements the reconnector protocol for lnd.
    """

    def reconnect_channels(
        self, max_blocks_to_expiry: int, include_inactive: bool
    ) -> None:

        # max block height for an htlc to be stuck
        max_expiry = self.block_height + max_blocks_to_expiry

        # list of all (pub_key, htlc)-tuples
        htlcs: list[tuple[str, ln.HTLC]] = []

        # list of the hashlocks of all stuck htlcs
        stuck_htlcs: set[bytes] = set()

        # set of peers with inactive channels
        inactive_peers: set[str] = set()

        h: ln.HTLC

        # Collecting all inactive channels and channels with stuck htlcs.
        for channel in self.lnd.list_channels().channels:
            if not channel.active:
                inactive_peers.add(channel.remote_pubkey)

            for h in channel.pending_htlcs:
                htlcs.append((channel.remote_pubkey, h))

                if h.expiration_height > max_expiry:
                    continue

                stuck_htlcs.add(h.hash_lock)

        # Set of the pub_keys of all peers which need a reconnect.
        pub_keys: set[str] = set()
        if include_inactive:
            pub_keys = inactive_peers

        # Collecting incoming and outgoing channels for all stuck htlcs.
        for hash_lock in stuck_htlcs:
            for pub_key, h in htlcs:
                if h.hash_lock == hash_lock:
                    pub_keys.add(pub_key)

        # Reconnecting all pub key.
        for pub_key in pub_keys:

            # TODO: logger.status when 0.19.0 is released.
            logger.info(f"Reconnecting {pub_key=}")
            self.disconnect_peer(pub_key)

            # sleep a second to ensure that the peer is disconnected by the
            # backend. Observed a race during development using lnd 0.18.4
            time.sleep(1)

            self.connect_peer(pub_key)
