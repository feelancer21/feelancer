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


def _create_rpc_error_handler(
    eval_status: Callable[[grpc.StatusCode, str], None] | None = None
) -> Callable[[grpc.RpcError], None]:
    """
    Creates an RPC error handler that logs the error and optionally calls a
    service specific evaluation function.
    """

    def rpc_error_handler(e: grpc.RpcError) -> None:
        code: grpc.StatusCode = e.code()  # type: ignore
        details: str = e.details()  # type: ignore

        if eval_status is not None:
            eval_status(code, details)

        msg = f"RpcError code: {code}; details: {details}"
        logging.error(msg)
        logging.debug(e)
        raise e

    return rpc_error_handler


def default_error_handler(e: Exception) -> None:
    """
    Default error handling for exceptions which are not RPC related.
    """

    msg = f"unexpected error during rpc call: {e}"
    logging.error(msg)
    raise e


class RpcResponseHandler:
    def __init__(
        self,
        rpc_error_handler: Callable[[grpc.RpcError], None],
        error_handler: Callable[[Exception], None],
    ):

        self.rpc_error_handler = rpc_error_handler
        self.error_handler = error_handler

    def handle_rpc_errors(self, fnc):
        """Decorator to add more context to RPC errors"""

        @wraps(fnc)
        def wrapper(*args, **kwargs):
            try:
                return fnc(*args, **kwargs)
            except grpc.RpcError as e:
                self.rpc_error_handler(e)
            except Exception as e:
                self.error_handler(e)

        return wrapper

    @classmethod
    def with_eval_status(
        cls, eval_status: Callable[[grpc.StatusCode, str], None]
    ) -> RpcResponseHandler:
        """Creates an RpcResponseHandler that utilizes a specific evaluation
        function to handle service specific RPC errors.
        """

        return cls(_create_rpc_error_handler(eval_status), default_error_handler)


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
