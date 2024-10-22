from __future__ import annotations

from feelancer.grpc.client import RpcResponseHandler, SecureGrpcClient

from .grpc_generated import lightning_pb2 as ln
from .grpc_generated import lightning_pb2_grpc as lnrpc

lnd_resp_handler = RpcResponseHandler()
lnd_handle_rpc_errors = lnd_resp_handler.handle_rpc_errors


def set_chan_point(chan_point_str: str, chan_point: ln.ChannelPoint) -> None:
    txid, out_index = chan_point_str.split(":")
    txid_reversed = bytearray(bytes.fromhex(txid))
    txid_reversed.reverse()
    chan_point.funding_txid_bytes = bytes(txid_reversed)
    chan_point.output_index = int(out_index)


class LndGrpc(SecureGrpcClient):
    @property
    def _ln_stub(self) -> lnrpc.LightningStub:
        """
        Create a ln_stub dynamically to ensure channel freshness

        If we make a call to the Lightning RPC service when the wallet
        is locked or the server is down we will get back an RpcError with
        StatusCode.UNAVAILABLE which will make the channel unusable.
        To ensure the channel is usable we create a new one for each request.
        """

        return lnrpc.LightningStub(self._channel)

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

        if max_htlc_msat:
            req.max_htlc_msat = max_htlc_msat

        if min_htlc_msat:
            req.min_htlc_msat = min_htlc_msat
            req.min_htlc_msat_specified = True

        if chan_point:
            c = req.chan_point
            set_chan_point(chan_point, c)

        if any([inbound_base_fee_msat, inbound_fee_rate_ppm]):
            infee = req.inbound_fee

            if inbound_base_fee_msat:
                infee.base_fee_msat = inbound_base_fee_msat

            if inbound_fee_rate_ppm:
                infee.fee_rate_ppm = inbound_fee_rate_ppm

        return self._ln_stub.UpdateChannelPolicy(req)


def update_failure_name(num) -> str:
    return ln.UpdateFailure.Name(num)
