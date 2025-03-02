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
SLEEP_RECON = 5


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


# Retrying using the same grpc channel.
_channel_retry_handler = create_retry_handler(
    exceptions_retry=(Exception,),
    exceptions_raise=(LocallyCancelled,),
    max_retries=1,
    delay=10,
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

        self._message_queues: list[queue.Queue[T | Exception]] = []
        self._stream: Iterable[T] | None = None

        # Indicates whether there is a subscriber for the messages.
        self._is_subscribed: bool = False

        # Indicates whether the dispatcher is receiving messages from the source
        self._is_receiving: bool = False

        self._register_sync_starter(self._start)
        self._register_sync_stopper(self._stop)

    def subscribe(
        self,
        convert: Callable[[T, bool], V],
        get_recon_source: Callable[..., Generator[T]] | None = None,
    ) -> Generator[V]:
        """Returns a generator for all new incoming messages."""

        # Creates a mew queue and makes it available for the subscriber of the
        # grpc stream.
        q = queue.Queue()
        self._message_queues.append(q)

        self._is_subscribed = True

        while True:

            # Blocking until we know that the dispatcher is receiving messages
            # from the source. Otherwise reconciliation would start too
            # early.
            while not (self._is_receiving or self._is_stopped):
                pass

            if self._is_stopped:
                return None

            # Indicates the subscriber whether the messages were created during
            # reconciliation. In this way he can decide if the messages are
            # processed or not.
            in_recon = False

            # If there are messages from the reconciliation source, we yield them
            # first.
            if get_recon_source is not None:
                logging.info(f"{self._name} reconciliation started")
                in_recon = True

                # Sleeping a little bit before fetching from the reconciliation
                # source.
                time.sleep(SLEEP_RECON)
                for m in get_recon_source():
                    yield convert(m, in_recon)

                    # Safety check for big reconciliation sources.
                    if self._is_stopped:
                        return

                logging.info(f"{self._name} reconciliation stage 1 finished")

            # We yield the messages from the stream until we got an exception.
            while True:
                m = q.get()

                # If the service is stopped by the user we return early
                if isinstance(m, LocallyCancelled):
                    return None

                # If there is an unknown exception we break here and start
                # with a new reconciliation. Maybe data have been run out of sync.
                if isinstance(m, Exception):
                    break

                yield convert(m, in_recon)

                # From the first time the queue is empty, the caller will only
                # receive messages from the stream.
                if q.qsize() == 0 and in_recon is True:
                    logging.info(f"{self._name} reconciliation finished")
                    in_recon = False

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

        # We have a subscriber. We can start the stream
        try:
            # The stream initializer allows creating rpc streams in the same
            # grpc channel. We have to init it outside _channel_retry_handler.
            stream_initializer = self._new_stream_initializer()

            self._start_stream(stream_initializer)

        # Unexpected errors are raised
        except Exception as e:

            # Signaling the end of the queue to the consumers.
            self._put_to_queues(e)

            if isinstance(e, LocallyCancelled):
                # User ended the stream.
                logging.debug(f"{self._name} cancelled: {e}")

                return None

            raise e

    @_channel_retry_handler
    def _start_stream(self, stream_initializer: grpc.UnaryStreamMultiCallable) -> None:

        # Creating a new stream in the grpc channel
        self._stream = stream_initializer(self._request)
        logging.debug(f"{self._name} stream started")

        try:
            self._is_receiving = True
            # Receiving of the grp messages
            for m in handle_rpc_stream(self._stream):  # type: ignore
                self._put_to_queues(m)

        except Exception as e:
            raise e

        finally:
            self._stream = None
            self._is_receiving = False

    def _stop(self) -> None:
        """Stops receiving of the messages from the upstream."""

        if self._stream is not None:
            self._stream.cancel()  # type: ignore

    def _put_to_queues(self, data: T | Exception) -> None:
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
