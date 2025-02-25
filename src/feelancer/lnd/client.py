from __future__ import annotations

import re
from collections.abc import Generator, Iterable, Sequence

import grpc

from feelancer.base import BaseServer
from feelancer.grpc.client import (
    Paginator,
    RpcResponseHandler,
    SecureGrpcClient,
    StreamDispatcher,
)

from .grpc_generated import lightning_pb2 as ln
from .grpc_generated import lightning_pb2_grpc as lnrpc
from .grpc_generated import router_pb2 as rt
from .grpc_generated import router_pb2_grpc as rtrpc

PAGINATOR_MAX_FORWARDING_EVENTS = 10000
PAGINATOR_MAX_PAYMENTS = 10000


class EdgeNotFound(Exception): ...


class PeerNotConnected(Exception): ...


class PeerAlreadyConnected(Exception): ...


class DialProxFailed(Exception): ...


class EOF(Exception): ...


def _eval_lnd_rpc_status(code: grpc.StatusCode, details: str) -> bool:
    """
    Callable which evaluates lnd specific grpc error based on StatusCode and
    details. If a criteria is matched, a specific exception is raised or True
    is returned. If no criteria is matched False is returned.
    """

    edge_not_found = "edge not found"
    wallet_unlocked = "wallet locked, unlock it to enable full RPC access"
    wrong_macaroon = "verification failed: signature mismatch after caveat verification"

    if code == grpc.StatusCode.UNKNOWN:
        if details == edge_not_found:
            raise EdgeNotFound(details)

        if details == "EOF":
            raise EOF(details)

        if re.match(r"unable to disconnect peer: peer (.*) is not connected", details):
            raise PeerNotConnected(details)

        if re.match(r"already connected to peer: (.*)", details):
            raise PeerAlreadyConnected(details)

        if re.match(
            r"dial proxy failed(.*)",
            details,
        ):
            raise DialProxFailed(details)

        # Caller should raise the original exception if the wallet is unlocked
        # or the macaroon is wrong.
        if details in [wallet_unlocked, wrong_macaroon]:
            return True

    return False


lnd_resp_handler = RpcResponseHandler.with_eval_status(_eval_lnd_rpc_status)
lnd_handle_rpc_errors = lnd_resp_handler.handle_rpc_errors


def set_chan_point(chan_point_str: str, chan_point: ln.ChannelPoint) -> None:
    txid, out_index = chan_point_str.split(":")
    txid_reversed = bytearray(bytes.fromhex(txid))
    txid_reversed.reverse()
    chan_point.funding_txid_bytes = bytes(txid_reversed)
    chan_point.output_index = int(out_index)


class LndForwardingEventPaginator(Paginator[ln.ForwardingEvent]): ...


class LndPaymentPaginator(Paginator[ln.Payment]): ...


class LndGrpc(SecureGrpcClient, BaseServer):

    def __init__(
        self,
        ip_address: str,
        credentials: grpc.ChannelCredentials,
        **kwargs,
    ) -> None:
        SecureGrpcClient.__init__(self, ip_address, credentials)

        # Responsible for dispatching realtime streams form the grpc server
        # to internal services.
        BaseServer.__init__(self, **kwargs)

        # Dispatcher for tracking payments. New class for logging purposes.
        class LndPaymentDispatcher(StreamDispatcher[ln.Payment]): ...

        self.track_payments_dispatcher = LndPaymentDispatcher(
            producer=self.track_payments
        )

        self._register_sub_server(self.track_payments_dispatcher)

    @property
    def _ln_stub(self) -> lnrpc.LightningStub:
        """
        Creates a LightningStub

        If we make a call to the Lightning RPC service when the wallet
        is locked or the server is down we will get back an RpcError with
        StatusCode.UNAVAILABLE which will make the channel unusable.
        To ensure the channel is usable we create a new one for each request.
        """

        return lnrpc.LightningStub(self._channel)

    @property
    def _router_stub(self) -> rtrpc.RouterStub:
        """
        Creates a RouterStub
        """

        return rtrpc.RouterStub(self._channel)

    @lnd_handle_rpc_errors
    def connect_peer(
        self,
        pub_key: str,
        host: str,
        perm: bool | None = None,
        timeout: int | None = None,
    ) -> ln.ConnectPeerResponse:
        """
        Calls lnrpc.ConnectPeer

        ConnectPeer attempts to establish a connection to a remote peer. This is
        at the networking level, and is used for communication between nodes.
        This is distinct from establishing a channel with a peer.
        """
        ...

        req = ln.ConnectPeerRequest()
        ln_addr = req.addr

        ln_addr.pubkey = pub_key
        ln_addr.host = host

        if perm is not None:
            req.perm = perm

        if timeout is not None:
            req.timeout = timeout

        return self._ln_stub.ConnectPeer(req)

    @lnd_handle_rpc_errors
    def disconnect_peer(self, pub_key: str) -> ln.DisconnectPeerResponse:
        """
        Calls lnrp.DisconnectPeer

        DisconnectPeer attempts to disconnect one peer from another identified
        by a given pubKey. In the case that we currently have a pending or
        active channel with the target peer, then this action will be not be
        allowed.
        """
        ...

        return self._ln_stub.DisconnectPeer(ln.DisconnectPeerRequest(pub_key=pub_key))

    @lnd_handle_rpc_errors
    def get_chan_info(self, chan_id: int) -> ln.ChannelEdge:
        """
        Calls lnrpc.GetChanInfo

        GetChanInfo returns the latest authenticated network announcement for the
        given channel identified by its channel ID: an 8-byte integer which
        uniquely identifies the location of transaction's funding output within
        the blockchain.
        """
        return self._ln_stub.GetChanInfo(ln.ChanInfoRequest(chan_id=chan_id))

    @lnd_handle_rpc_errors
    def get_info(self) -> ln.GetInfoResponse:
        """
        Calls lnrpc.GetInfo

        GetInfo returns general information concerning the lightning node
        including it's identity pubkey, alias, the chains it is connected to,
        and information concerning the number of open+pending channels.
        """

        return self._ln_stub.GetInfo(ln.GetInfoRequest())

    @lnd_handle_rpc_errors
    def get_node_info(
        self, pub_key: str, include_channels: bool = False
    ) -> ln.NodeInfo:
        """
        Calls lnrpc.GetNodeInfo

        GetNodeInfo returns the latest advertised, aggregated, and authenticated
        channel information for the specified node identified by its public key.
        """

        req = ln.NodeInfoRequest(pub_key=pub_key, include_channels=include_channels)
        return self._ln_stub.GetNodeInfo(req)

    @lnd_handle_rpc_errors
    def list_channels(self) -> ln.ListChannelsResponse:
        """
        Calls lnrpc.ListChannels

        ListChannels returns a description of all the open channels that this
        node is a participant in.
        """

        return self._ln_stub.ListChannels(ln.ListChannelsRequest())

    @lnd_handle_rpc_errors
    def update_channel_policy(
        self,
        base_fee_msat: int,
        fee_rate_ppm: int,
        time_lock_delta: int,
        _global: bool = False,
        max_htlc_msat: int | None = None,
        min_htlc_msat: int | None = None,
        inbound_base_fee_msat: int | None = None,
        inbound_fee_rate_ppm: int | None = None,
        chan_point: str | None = None,
    ) -> ln.PolicyUpdateResponse:
        """
        Calls lnrpc.UpdateChannelPolicy

        UpdateChannelPolicy allows the caller to update the fee schedule and
        channel policies for all channels globally, or a particular channel.
        """

        req = ln.PolicyUpdateRequest()
        req.base_fee_msat = base_fee_msat
        req.fee_rate_ppm = fee_rate_ppm
        req.time_lock_delta = time_lock_delta
        setattr(req, "global", _global)

        if max_htlc_msat is not None:
            req.max_htlc_msat = max_htlc_msat

        if min_htlc_msat is not None:
            req.min_htlc_msat = min_htlc_msat
            req.min_htlc_msat_specified = True

        if chan_point is not None:
            c = req.chan_point
            set_chan_point(chan_point, c)

        if any([inbound_base_fee_msat is not None, inbound_fee_rate_ppm is not None]):
            infee = req.inbound_fee

            if inbound_base_fee_msat is not None:
                infee.base_fee_msat = inbound_base_fee_msat

            if inbound_fee_rate_ppm is not None:
                infee.fee_rate_ppm = inbound_fee_rate_ppm

        return self._ln_stub.UpdateChannelPolicy(req)

    @lnd_handle_rpc_errors
    def track_payments(self, no_inflight_updates: bool = False) -> Iterable[ln.Payment]:

        req = rt.TrackPaymentsRequest()
        req.no_inflight_updates = no_inflight_updates

        return self._router_stub.TrackPayments(req)

    def paginate_forwarding_events(
        self,
        num_max_events: int | None = None,
        index_offset: int = 0,
        start_time: int = 0,
        end_time: int = 0,
    ) -> Generator[ln.ForwardingEvent]:

        def _read(
            d: ln.ForwardingHistoryResponse,
        ) -> tuple[Sequence[ln.ForwardingEvent], int]:
            return d.forwarding_events, d.last_offset_index

        def _set(d: ln.ForwardingHistoryRequest, offset: int, max: int) -> None:
            d.index_offset = offset
            d.num_max_events = max

        paginator = LndForwardingEventPaginator(
            producer=self._ln_stub.ForwardingHistory,
            request=ln.ForwardingHistoryRequest,
            max_responses=PAGINATOR_MAX_FORWARDING_EVENTS,
            read_response=_read,
            set_request=_set,
        )

        return paginator.request(
            num_max_events, index_offset, start_time=start_time, end_time=end_time
        )

    def paginate_payments(
        self, max_payments: int | None = None, index_offset: int = 0, **kwargs
    ) -> Generator[ln.Payment]:

        def _read(d: ln.ListPaymentsResponse) -> tuple[Sequence[ln.Payment], int]:
            return d.payments, d.last_index_offset

        def _set(d: ln.ListPaymentsRequest, offset: int, max: int) -> None:
            d.index_offset = offset
            d.max_payments = max

        paginator = LndPaymentPaginator(
            producer=self._ln_stub.ListPayments,
            request=ln.ListPaymentsRequest,
            max_responses=PAGINATOR_MAX_PAYMENTS,
            read_response=_read,
            set_request=_set,
        )

        return paginator.request(max_payments, index_offset)


def update_failure_name(num) -> str:
    return ln.UpdateFailure.Name(num)
