from __future__ import annotations

import codecs
import logging
import os
import queue
import time
from collections.abc import Callable, Generator, Iterable
from functools import wraps
from typing import Generic, TypeVar

import grpc

from feelancer.base import BaseServer, default_retry_handler

DEFAULT_MESSAGE_SIZE_MB = 50 * 1024 * 1024
DEFAULT_MAX_CONNECTION_IDLE_MS = 30000
DEFAULT_SLEEP_ON_RPC_ERROR = 60

T = TypeVar("T")
V = TypeVar("V")

os.environ["GRPC_SSL_CIPHER_SUITES"] = "HIGH+ECDSA"


class LocallyCancelled(Exception): ...


def _create_rpc_error_handler(
    eval_status: Callable[[grpc.StatusCode, str], bool] | None = None
) -> Callable[[grpc.RpcError], None]:
    """
    Creates an RPC error handler that logs the error and optionally calls a
    service specific evaluation function.
    """

    def rpc_error_handler(e: grpc.RpcError) -> None:
        code: grpc.StatusCode = e.code()  # type: ignore
        details: str = e.details()  # type: ignore

        msg = f"RpcError code: {code}; details: {details}"
        if eval_status is not None:

            # If the eval function returns True, we raise the original grpc error
            if eval_status(code, details) is True:
                logging.error(msg)
                raise e

        # Can occur during rpc streams, when the user cancels the stream.
        if code == grpc.StatusCode.CANCELLED:
            if details == "Locally cancelled by application!":
                raise LocallyCancelled(details)

        # We raise the exception if the server is not available.
        logging.error(msg)
        if code == grpc.StatusCode.UNAVAILABLE:
            raise e

        # For unknown errors we log the exception, hence it must not be done
        # by the caller.
        logging.exception(e)
        raise e

    return rpc_error_handler


def default_error_handler(e: Exception) -> None:
    """
    Default error handling for exceptions which are not RPC related.
    """

    msg = f"unexpected error during rpc call: {e}"
    logging.error(msg)
    raise e


def handle_rpc_stream(stream: Iterable[T]) -> Generator[T]:
    """
    Decorator for handling errors during a rpc stream.
    """
    rpc_handler = _create_rpc_error_handler()
    try:
        yield from stream
    except grpc.RpcError as e:
        rpc_handler(e)


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
        cls, eval_status: Callable[[grpc.StatusCode, str], bool]
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


class StreamDispatcher(Generic[T], BaseServer):
    """
    Receives grpc messages of Type T from an stream and handles the errors.
    The message are distributors to all subscribers of the dispatchers.
    """

    def __init__(self, producer: Callable[..., Iterable[T]], **kwargs) -> None:

        BaseServer.__init__(self, **kwargs)

        self._producer: Callable[..., Iterable[T]] = producer

        self._message_queues: list[queue.Queue[T | None]] = []
        self._stream: Iterable[T] | None = None
        self._is_subscribed: bool = False

        self._register_sync_starter(self._start)
        self._register_sync_stopper(self._stop)

    def subscribe(
        self, convert: Callable[[T], V], filter: Callable[[T], bool] | None = None
    ) -> Generator[V]:
        """Returns a generator for all new incoming messages."""

        if self._is_stopped is True:
            return

        self._is_subscribed = True

        # Creates a mew queue and makes it available for the receiver of the grpc
        # messages.
        q = queue.Queue()
        self._message_queues.append(q)

        while True:
            m = q.get()

            # None is signals the end of the queue. We can break the loop.
            if m is None:
                break

            # If a filter is given, we check if the message is valid.
            if filter is not None and filter(m) is False:
                continue

            yield convert(m)

    @default_retry_handler
    def _start(self) -> None:
        """
        Starts receiving messages from the upstream. It is blocked until first
        subscriber has registered.
        """

        # blocking until there is a first subscription or the server is stopped.
        while not (self._is_subscribed or self._is_stopped):
            pass

        # Returning early if the server is stopped.
        if self._is_stopped:
            return None

        # We have a subscriber. We can start the stream.
        while True:
            try:
                logging.info(f"Starting {self._name}...")
                self._stream = self._producer()

                # Receiving of the grp messages
                for m in handle_rpc_stream(self._stream):
                    self._put_to_queues(m)

            except LocallyCancelled as e:
                # User ended the stream.
                logging.debug(f"{self._name} cancelled: {e}")

                # Signaling the end of the queue to the consumer
                self._put_to_queues(None)

                break

            # On RpcErrors we are doing a retry after 60s. E.g. server not available.
            except grpc.RpcError as e:
                logging.error(
                    f"Rpc error in {self._name} occurred; {e=}; "
                    f"retry in {DEFAULT_SLEEP_ON_RPC_ERROR}s"
                )
                time.sleep(DEFAULT_SLEEP_ON_RPC_ERROR)

            # Unexpected errors are raised
            except Exception as e:
                raise e

            self._stream = None

    def _stop(self) -> None:
        """Stops receiving of the messages from the upstream."""

        if self._stream is not None:
            self._stream.cancel()  # type: ignore

    def _put_to_queues(self, data: T | None) -> None:
        """Puts a message to each queue."""
        for q in self._message_queues:
            q.put(data)
