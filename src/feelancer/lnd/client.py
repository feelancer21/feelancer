from __future__ import annotations

import codecs
import logging
import os
from functools import wraps
from typing import Callable

import grpc

from .grpc_generated import lightning_pb2 as ln
from .grpc_generated import lightning_pb2_grpc as lnrpc

DEFAULT_MESSAGE_SIZE_MB = 50 * 1024 * 1024
DEFAULT_MAX_CONNECTION_IDLE_MS = 30000

os.environ["GRPC_SSL_CIPHER_SUITES"] = "HIGH+ECDSA"


class RpcResponseHandler:
    def __init__(self):
        def on_rpc_error(e: grpc.RpcError) -> None:
            code = e.code()  # type: ignore
            details = e.details()  # type: ignore
            msg = f"RpcError code: {code}; details: {details}"
            logging.error(msg)
            logging.debug(e)
            raise e

        def on_error(e: Exception) -> None:
            msg = f"unexpected error during rpc call: {e}"
            logging.error(msg)
            raise e

        self.on_rpc_error: Callable[[grpc.RpcError], None] = on_rpc_error
        self.on_error: Callable[[Exception], None] = on_error

    def handle_rpc_errors(self, fnc):
        """Decorator to add more context to RPC errors"""

        @wraps(fnc)
        def wrapper(*args, **kwargs):
            try:
                return fnc(*args, **kwargs)
            except grpc.RpcError as e:
                self.on_rpc_error(e)
            except Exception as e:
                self.on_error(e)

        return wrapper


resp_handler = RpcResponseHandler()
handle_rpc_errors = resp_handler.handle_rpc_errors


class MacaroonMetadataPlugin(grpc.AuthMetadataPlugin):
    """Metadata plugin to include macaroon in metadata of each RPC request"""

    def __init__(self, macaroon):
        self.macaroon = macaroon

    def __call__(self, context, callback):
        callback([("macaroon", self.macaroon)], None)


class SecureGrpc:
    def __init__(self, ip_address: str, credentials: grpc.ChannelCredentials):
        self.channel_options = [
            ("grpc.max_message_length", DEFAULT_MESSAGE_SIZE_MB),
            ("grpc.max_receive_message_length", DEFAULT_MESSAGE_SIZE_MB),
            ("grpc.max_connection_idle_ms", DEFAULT_MAX_CONNECTION_IDLE_MS),
        ]
        self.ip_address = ip_address
        self._credentials = credentials

    @classmethod
    def from_file(cls, ip_address: str, cert_filepath: str, macaroon_filepath: str):
        tls_certificate = open(cert_filepath, "rb").read()
        ssl_credentials = grpc.ssl_channel_credentials(tls_certificate)

        macaroon = codecs.encode(open(macaroon_filepath, "rb").read(), "hex")

        metadata_plugin = MacaroonMetadataPlugin(macaroon)
        auth_credentials = grpc.metadata_call_credentials(metadata_plugin)

        combined_credentials = grpc.composite_channel_credentials(
            ssl_credentials, auth_credentials
        )
        return cls(ip_address, combined_credentials)

    @property
    def _channel(self):
        return grpc.secure_channel(
            self.ip_address, self._credentials, self.channel_options
        )


def _get_chan_point(chan_point: str) -> ln.ChannelPoint:
    txid, out_index = chan_point.split(":")
    txid_reversed = bytearray(bytes.fromhex(txid))
    txid_reversed.reverse()
    return ln.ChannelPoint(
        funding_txid_bytes=bytes(txid_reversed), output_index=int(out_index)
    )


class LndGrpc(SecureGrpc):
    @property
    def _ln_stub(self) -> lnrpc.LightningStub:
        """Create a ln_stub dynamically to ensure channel freshness

        If we make a call to the Lightning RPC service when the wallet
        is locked or the server is down we will get back an RpcError with
        StatusCode.UNAVAILABLE which will make the channel unusable.
        To ensure the channel is usable we create a new one for each request.
        """

        return lnrpc.LightningStub(self._channel)

    def get_chan_info(self, chan_id: int) -> ln.ChannelEdge:
        """
        Calls lnrpc.GetChanInfo

        GetChanInfo returns the latest authenticated network announcement for the
        given channel identified by its channel ID: an 8-byte integer which
        uniquely identifies the location of transaction's funding output within
        the blockchain.
        """
        return self._ln_stub.GetChanInfo(ln.ChanInfoRequest(chan_id=chan_id))

    @handle_rpc_errors
    def get_info(self) -> ln.GetInfoResponse:
        """
        Calls lnrpc.GetInfo

        GetInfo returns general information concerning the lightning node
        including it's identity pubkey, alias, the chains it is connected to,
        and information concerning the number of open+pending channels.
        """

        return self._ln_stub.GetInfo(ln.GetInfoRequest())

    @handle_rpc_errors
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

    @handle_rpc_errors
    def list_channels(self) -> ln.ListChannelsResponse:
        """
        Calls lnrpc.ListChannels

        ListChannels returns a description of all the open channels that this
        node is a participant in.
        """

        return self._ln_stub.ListChannels(ln.ListChannelsRequest())

    @handle_rpc_errors
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

        kwargs = {
            "base_fee_msat": base_fee_msat,
            "fee_rate_ppm": fee_rate_ppm,
            "time_lock_delta": time_lock_delta,
            "global": _global,
        }
        if max_htlc_msat:
            kwargs["max_htlc_msat"] = max_htlc_msat

        if min_htlc_msat:
            kwargs["min_htlc_msat"] = min_htlc_msat
            kwargs["min_htlc_msat_specified"] = True

        if chan_point:
            kwargs["chan_point"] = _get_chan_point(chan_point)

        ikwargs = {}
        if inbound_base_fee_msat:
            ikwargs["base_fee_msat"] = inbound_base_fee_msat

        if inbound_fee_rate_ppm:
            ikwargs["fee_rate_ppm"] = inbound_fee_rate_ppm

        if len(ikwargs) > 0:
            kwargs["inbound_fee"] = ln.InboundFee(**ikwargs)

        req = ln.PolicyUpdateRequest(**kwargs)
        return self._ln_stub.UpdateChannelPolicy(req)


def update_failure_name(num) -> str:
    return ln.UpdateFailure.Name(num)
