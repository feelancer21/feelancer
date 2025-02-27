from __future__ import annotations

import codecs
import logging
import os
import queue
import time
from collections.abc import Callable, Generator, Iterable, Sequence
from functools import wraps
from typing import Generic, TypeVar

import grpc
from google.protobuf.message import Message

from feelancer.base import BaseServer, create_retry_handler, default_retry_handler

DEFAULT_MESSAGE_SIZE_MB = 50 * 1024 * 1024
DEFAULT_MAX_CONNECTION_IDLE_MS = 30000
DEFAULT_KEEPALIVE_TIME_MS = 30000
DEFAULT_KEEPALIVE_TIMEOUT_MS = 20000
DEFAULT_SLEEP_ON_RPC_ERROR = 60

T = TypeVar("T", bound=Message)
U = TypeVar("U", bound=Message)
V = TypeVar("V")
W = TypeVar("W", bound=Message)


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
            ("grpc.keepalive_time_ms", DEFAULT_KEEPALIVE_TIME_MS),
            ("grpc.keepalive_timeout_ms", DEFAULT_KEEPALIVE_TIMEOUT_MS),
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


# Retrying using the same grpc channel
_channel_retry_handler = create_retry_handler(
    exceptions_retry=(Exception,),
    exceptions_raise=(LocallyCancelled,),
    max_retries=5,
    delay=15,
    min_tolerance_delta=120,
)


class StreamDispatcher(Generic[T], BaseServer):
    """
    Receives grpc messages of Type T from an stream and handles the errors.
    The message are distributors to all subscribers of the dispatchers.
    """

    def __init__(
        self,
        new_stream_initializer: Callable[..., grpc.UnaryStreamMultiCallable],
        request: Message,
        **kwargs,
    ) -> None:

        BaseServer.__init__(self, **kwargs)

        self._new_stream_initializer = new_stream_initializer
        self._request: Message = request

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

        # We have a subscriber. We can start the stream.
        while True:
            try:
                # Returning early if the server is stopped.
                if self._is_stopped:
                    return None

                # The stream initializer allows creating rpc streams in the same
                # grpc channel.
                stream_initializer = self._new_stream_initializer()

                self._start_stream(stream_initializer)

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

    @_channel_retry_handler
    def _start_stream(self, stream_initializer: grpc.UnaryStreamMultiCallable) -> None:

        # Creating a new stream in the grpc channel
        self._stream = stream_initializer(self._request)

        # Receiving of the grp messages
        for m in handle_rpc_stream(self._stream):  # type: ignore
            self._put_to_queues(m)
        print("stop")

    def _stop(self) -> None:
        """Stops receiving of the messages from the upstream."""

        if self._stream is not None:
            self._stream.cancel()  # type: ignore

    def _put_to_queues(self, data: T | None) -> None:
        """Puts a message to each queue."""
        for q in self._message_queues:
            q.put(data)


class Paginator(Generic[W]):
    # T is the type of the request message
    # V is the type of the response message
    # W is the type of the response data (some. submessage of V)

    def __init__(
        self,
        producer: grpc.UnaryUnaryMultiCallable,
        request: type[T],
        max_responses: int,
        read_response: Callable[[V], tuple[Sequence[W], int]],
        set_request: Callable[[T, int, int], None],
    ) -> None:
        self._producer = producer
        self._request = request
        self._max_responses = max_responses
        self._read_response = read_response
        self._set_request = set_request

    def request(
        self, max_events: int | None, offset: int = 0, **kwargs
    ) -> Generator[W]:

        events_open = max_events

        # Max number of events to request in the next call
        next_max = self._max_responses
        next_offset = offset

        while True:

            if events_open is not None and events_open < self._max_responses:
                next_max = events_open

            req = self._request(**kwargs)
            self._set_request(req, next_offset, next_max)

            resp = self._producer(req)

            data, next_offset = self._read_response(resp)

            yield from data

            # Next call would not return any more events
            if len(data) < self._max_responses:
                break

            # If there is no limit on the number of events, we continue until the
            # last event is reached.
            if events_open is None:
                continue

            events_open -= len(data)
            if events_open == 0:
                break
