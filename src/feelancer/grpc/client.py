from __future__ import annotations

import codecs
import logging
import os
from collections.abc import Callable
from functools import wraps

import grpc

DEFAULT_MESSAGE_SIZE_MB = 50 * 1024 * 1024
DEFAULT_MAX_CONNECTION_IDLE_MS = 30000

os.environ["GRPC_SSL_CIPHER_SUITES"] = "HIGH+ECDSA"


def default_on_rpc_error(e: grpc.RpcError) -> None:
    """
    Default error handling for rpc errors.
    """

    code = e.code()  # type: ignore
    details = e.details()  # type: ignore
    msg = f"RpcError code: {code}; details: {details}"
    logging.error(msg)
    logging.debug(e)
    raise e


def default_on_error(e: Exception) -> None:
    """
    Default error handling for exceptions.
    """

    msg = f"unexpected error during rpc call: {e}"
    logging.error(msg)
    raise e


class RpcResponseHandler:
    def __init__(
        self,
        on_rpc_error: Callable[[grpc.RpcError], None] = default_on_rpc_error,
        on_error: Callable[[Exception], None] = default_on_error,
    ):

        self.on_rpc_error = on_rpc_error
        self.on_error = on_error

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


class MacaroonMetadataPlugin(grpc.AuthMetadataPlugin):
    """Metadata plugin to include macaroon in metadata of each RPC request"""

    def __init__(self, macaroon):
        self.macaroon = macaroon

    def __call__(self, context, callback):
        callback([("macaroon", self.macaroon)], None)


class SecureGrpcClient:
    def __init__(self, ip_address: str, credentials: grpc.ChannelCredentials):
        self.channel_options = [
            ("grpc.max_message_length", DEFAULT_MESSAGE_SIZE_MB),
            ("grpc.max_receive_message_length", DEFAULT_MESSAGE_SIZE_MB),
            ("grpc.max_connection_idle_ms", DEFAULT_MAX_CONNECTION_IDLE_MS),
        ]
        self.ip_address = ip_address
        self._credentials = credentials

    @classmethod
    def from_file(
        cls, ip_address: str, cert_filepath: str, macaroon_filepath: str | None
    ):
        tls_certificate = open(cert_filepath, "rb").read()
        ssl_credentials = grpc.ssl_channel_credentials(tls_certificate)

        # We can return early if no authentication is required.
        if not macaroon_filepath:
            return cls(ip_address, ssl_credentials)

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
