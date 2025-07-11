from __future__ import annotations

import codecs
import logging
import os
import queue
import time
from collections.abc import Callable, Generator, Iterable, Sequence
from functools import wraps
from typing import Generic, Protocol, TypeVar

import grpc
from google.protobuf.message import Message

from feelancer.base import BaseServer
from feelancer.retry import create_retry_handler, default_retry_handler

DEFAULT_MESSAGE_SIZE_MB = 200 * 1024 * 1024
DEFAULT_MAX_CONNECTION_IDLE_MS = 30000
DEFAULT_KEEPALIVE_TIME_MS = 30000
DEFAULT_KEEPALIVE_TIMEOUT_MS = 20000
SLEEP_RECON = 5


T = TypeVar("T", bound=Message)
U = TypeVar("U", bound=Message)
V = TypeVar("V", covariant=True)
W = TypeVar("W", bound=Message)


os.environ["GRPC_SSL_CIPHER_SUITES"] = "HIGH+ECDSA"


class LocallyCancelled(Exception): ...


class DeadlineExceeded(Exception): ...


class RpcResponseHandler:
    def __init__(self, eval_error: Callable[[grpc.RpcError, str | None], None]):

        # eval_error is a client specific function which evaluates a RpcError. It
        # takes the RpcError and an optional function name as arguments.
        self._eval_error = eval_error
        self._logger = logging.getLogger(self.__module__)

    def decorator_rpc_unary(self, fnc):
        """
        Decorator for handling error for an unary rpc.
        """

        @wraps(fnc)
        def wrapper(*args, **kwargs):
            try:
                return fnc(*args, **kwargs)
            except grpc.RpcError as e:
                self.rpc_error_handler(
                    e, self._msg_body(fnc, *args, **kwargs), fnc.__name__
                )
            except Exception as e:
                self.exception_handler(e, self._msg_body(fnc, *args, **kwargs))

        return wrapper

    def decorator_rpc_stream(self, fnc):
        """
        Decorator for handling errors for a stream rpc. It handles the raised
        errors when subscribing to the stream.
        """

        @wraps(fnc)
        def wrapper(*args, **kwargs):
            try:
                stream = fnc(*args, **kwargs)
                yield from stream
            except grpc.RpcError as e:
                self.rpc_error_handler(
                    e, self._msg_body(fnc, *args, **kwargs), fnc.__name__
                )
            except Exception as e:
                self.exception_handler(e, self._msg_body(fnc, *args, **kwargs))

        return wrapper

    def create_handle_rpc_stream(self, name) -> Callable[[Iterable[T]], Generator[T]]:
        """
        Creates a callable which handles the errors during a stream rpc.
        """

        def handle_rpc_stream(stream: Iterable[T]) -> Generator[T]:
            msg = f"Error during '{name}'"
            try:
                yield from stream
            except grpc.RpcError as e:
                self.rpc_error_handler(e, msg, name)
            except Exception as e:
                self.exception_handler(e, msg)

        return handle_rpc_stream

    def _msg_body(self, fnc: Callable, *args, **kwargs) -> str:
        """
        Creates more context for the error message with the function name and
        the arguments.
        """

        return f"Error during '{fnc.__name__}'; args: {args=}; kwargs: {kwargs=}"

    def exception_handler(self, e: Exception, msg_body: str) -> None:
        """
        Default error handling for exceptions which are not RPC related.
        """
        self._logger.error(msg_body)
        raise e

    def rpc_error_handler(
        self, e: grpc.RpcError, msg_body: str, func_name: str | None
    ) -> None:

        # Eval the error to raise a specific exception.
        self._eval_error(e, func_name)

        code: grpc.StatusCode = e.code()  # type: ignore
        details: str = e.details()  # type: ignore

        msg = f"{msg_body}; RpcError code: {code}; details: {details}"

        # Can occur during rpc streams, when the user cancels the stream.
        if code == grpc.StatusCode.CANCELLED:
            if details == "Locally cancelled by application!":
                raise LocallyCancelled(details)

        if code == grpc.StatusCode.DEADLINE_EXCEEDED:
            raise DeadlineExceeded(details)

        # In other cases we raise the exception.
        self._logger.error(msg)
        raise e


class MacaroonMetadataPlugin(grpc.AuthMetadataPlugin):
    """Metadata plugin to include macaroon in metadata of each RPC request"""

    def __init__(self, macaroon):
        self.macaroon = macaroon

    def __call__(self, context, callback: Callable):
        callback([("macaroon", self.macaroon)], None)


class SecureGrpcClient:
    def __init__(
        self,
        ip_address: str,
        credentials: grpc.ChannelCredentials,
        grpc_max_message_length: int = DEFAULT_MESSAGE_SIZE_MB,
        grpc_max_receive_message_length: int = DEFAULT_MESSAGE_SIZE_MB,
        grpc_max_connection_idle_ms: int = DEFAULT_MAX_CONNECTION_IDLE_MS,
        grpc_keepalive_time_ms: int = DEFAULT_KEEPALIVE_TIME_MS,
        grpc_keepalive_timeout_ms: int = DEFAULT_KEEPALIVE_TIMEOUT_MS,
        **kwargs,
    ):
        self.channel_options = [
            ("grpc.max_message_length", grpc_max_message_length),
            ("grpc.max_receive_message_length", grpc_max_receive_message_length),
            ("grpc.max_connection_idle_ms", grpc_max_connection_idle_ms),
            ("grpc.keepalive_time_ms", grpc_keepalive_time_ms),
            ("grpc.keepalive_timeout_ms", grpc_keepalive_timeout_ms),
        ]
        self.ip_address = ip_address
        self._credentials = credentials

    @classmethod
    def from_file(
        cls,
        ip_address: str,
        cert_filepath: str,
        macaroon_filepath: str | None,
        **kwargs,
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
        return cls(ip_address, combined_credentials, **kwargs)

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


class ReconSource(Generic[V], Protocol):

    def items(self) -> Generator[V]:
        """
        Generates the messages for the reconciliation.
        """
        ...

    def stop(self) -> None:
        """
        Stops the generation of items.
        """
        ...


class StreamDispatcher(Generic[T], BaseServer):
    """
    Receives grpc messages of Type T from an stream and handles the errors.
    The message are distributors to all subscribers of the dispatchers.
    """

    def __init__(
        self,
        new_stream_initializer: Callable[..., grpc.UnaryStreamMultiCallable],
        request: Message,
        handle_rpc_stream: Callable[[Iterable[T]], Generator[T]],
        **kwargs,
    ) -> None:

        BaseServer.__init__(self, **kwargs)

        # Want to have the class name in the logger name
        self._logger: logging.Logger = logging.getLogger(
            self.__module__ + "." + self.__class__.__name__
        )

        self._new_stream_initializer = new_stream_initializer
        self._request: Message = request

        self._message_queues: list[queue.Queue[T | Exception]] = []
        self._stream: Iterable[T] | None = None
        self._handle_rpc_stream = handle_rpc_stream

        # Indicates whether there is a subscriber for the messages.
        self._is_subscribed: bool = False

        # Indicates whether the dispatcher is receiving messages from the source
        self._is_receiving: bool = False

        self._register_sync_starter(self._start)
        self._register_sync_stopper(self._stop)

    def subscribe(
        self,
        convert: Callable[[T, bool], Generator[V]],
        get_recon_source: Callable[..., ReconSource[V]] | None = None,
    ) -> Callable[..., Generator[V]]:
        """
        Returns a callable which starts a stream of all received messages
        converted to the type V.
        """

        # Creates a mew queue and makes it available for the subscriber of the
        # grpc stream.
        q: queue.Queue[T | Exception] = queue.Queue()
        self._message_queues.append(q)

        self._is_subscribed = True

        # We return a callable which enables the subscriber to start a stream.
        # It gives the caller the possibility to restart the stream with a
        # new reconciliation when necessary.
        def start_stream():
            return self._start_subscriber_stream(q, convert, get_recon_source)

        return start_stream

    def _start_subscriber_stream(
        self,
        q: queue.Queue[T | Exception],
        convert: Callable[[T, bool], Generator[V]],
        get_recon_source: Callable[..., ReconSource[V]] | None = None,
    ) -> Generator[V]:
        """Returns a generator for all new incoming messages converted to V."""

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
                self._logger.info("Reconciliation started")
                in_recon = True

                # Sleeping a little bit before fetching from the reconciliation
                # source. This is to fill up the queue with messages from the stream.
                time.sleep(SLEEP_RECON)
                recon_source = get_recon_source()

                self._register_sync_stopper(recon_source.stop)

                if self._is_stopped:
                    return None

                yield from recon_source.items()

                self._logger.info("Reconciliation stage 1 finished")

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

                yield from convert(m, in_recon)

                # From the first time the queue is empty, the caller will only
                # receive messages from the stream.
                if q.qsize() == 0 and in_recon is True:
                    self._logger.info("Reconciliation finished")
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
                self._logger.debug(f"Stream cancelled: {e}")

                return None

            raise e

    @_channel_retry_handler
    def _start_stream(self, stream_initializer: grpc.UnaryStreamMultiCallable) -> None:

        # Creating a new stream in the grpc channel, decorates with an error handler
        self._stream = stream_initializer(self._request)

        self._logger.debug("Starting stream...")
        try:
            self._is_receiving = True
            # Receiving of the grp messages
            for m in self._handle_rpc_stream(self._stream):  # type: ignore
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
