# from queue import Queue

# from feelancer.lightning.client import Channel


# class LnQueues:
#     def __init__(self) -> None:
#         self.channels: Queue[dict[int, Channel]] = Queue()
#         self.block_height: Queue[int] = Queue()


# class LnQueueClient:
#     """
#     LnQueueClient is an implementation of a LightningClient which gets data over
#     Queues. It allows us to test the tool with artificial data without spinning up
#     lightning backend nodes. Provided data are cached until new data are
#     provided.
#     """

#     def __init__(self, pubkey_local: str, queues: LnQueues) -> None:
#         self._pubkey_local = pubkey_local
#         self._q = queues
#         self._channels: dict[int, Channel] = {}
#         self._block_height: int = 0

#     @property
#     def block_height(self) -> int:
#         if not self._q.block_height.empty():
#             self._block_height = self._q.block_height.get()
#         return self._block_height

#     @property
#     def channels(self) -> dict[int, Channel]:
#         if not (self._q.channels.empty()):
#             self._channels = self._q.channels.get()
#         return self._channels

#     def connect_peer(self, pub_key: str) -> None:
#         return None

#     def disconnect_peer(self, pub_key: str) -> None:
#         return None

#     @property
#     def pubkey_local(self) -> str:
#         return self._pubkey_local

#     def update_channel_policy(
#         self,
#         chan_point: str,
#         fee_rate_ppm: int,
#         base_fee_msat: int,
#         time_lock_delta: int,
#         inbound_fee_rate_ppm: int,
#         inbound_base_fee_msat: int,
#     ) -> None:
#         for c in self._channels.values():
#             if c.chan_point == chan_point and (p := c.policy_local):
#                 p.fee_rate_ppm = fee_rate_ppm
#                 p.base_fee_msat = base_fee_msat
#                 p.time_lock_delta = time_lock_delta
#                 p.inbound_fee_rate_ppm = inbound_fee_rate_ppm
#                 p.inbound_base_fee_msat = inbound_base_fee_msat

#     def start(self) -> None: ...

#     def stop(self) -> None: ...
