import datetime
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from typing import Generic, TypeVar

import pytz
from google.protobuf.message import Message
from sqlalchemy.orm import DeclarativeBase

from feelancer.event import stop_event
from feelancer.grpc.client import StreamConverter, StreamDispatcher
from feelancer.lightning.lnd import LNDClient

# from feelancer.lnd.client import LndGrpc
from feelancer.log import getLogger, stream_logger
from feelancer.retry import default_retry_handler
from feelancer.tracker.data import TrackerStore

# ORM objects to be stored in the database
T = TypeVar("T", bound=DeclarativeBase)
# grpc Message returned by lnd during the pre sync and the recon
# (e.g. ListPaymentsResponse)
U = TypeVar("U", bound=Message)
# grpc Message returned by lnd data stream (e.g. TrackPayments)
V = TypeVar("V", bound=Message)

CHUNK_SIZE = 1000
STREAM_LOGGER_INTERVAL = 100


class LndBaseTracker(Generic[T, U, V], ABC):
    def __init__(self, lnd: LNDClient, store: TrackerStore):

        self._lnd = lnd.lnd
        self._pub_key = lnd.pubkey_local
        self._store = store
        self._items_name = self._get_items_name()
        self._logger = getLogger(self.__module__)
        self._stream_logger = stream_logger(
            interval=STREAM_LOGGER_INTERVAL,
            items_name=self._items_name,
            logger=self._logger,
        )
        self._time_start: datetime.datetime | None = None

    @abstractmethod
    def _delete_orphaned_data(self) -> None:
        """
        Deletes orphaned data from the database.
        """

    @abstractmethod
    def _get_items_name(self) -> str:
        """
        The name of the items that are being tracked. Used for logging.
        """

    @abstractmethod
    def _pre_sync_source(self) -> StreamConverter[T, U] | None:
        """
        Source of items to be processed in the presync process.
        """

    @abstractmethod
    def _process_item_stream(self, item: V, recon_running: bool) -> Generator[T]:
        """
        Process an item from the LND API to a SQLAlchemy object after start.
        """

    @default_retry_handler
    def _pre_sync_start(self) -> None:
        """
        Starts the presync process with a retry handler.
        """

        if stop_event.is_set():
            return

        @self._stream_logger
        def pre_sync_stream() -> Generator[T]:
            source = self._pre_sync_source()
            if source is None:
                return None

            yield from source.items()

        self._store.db.add_chunks_from_iterable(
            pre_sync_stream(), chunk_size=CHUNK_SIZE
        )

    @default_retry_handler
    def _start_stream(self, get_new_stream: Callable[..., Generator[T]]) -> None:
        """
        Fetches the items from the subscription and stores them in the database.
        """

        self._time_start = datetime.datetime.now(pytz.utc)

        # In every retry we initialize a new generator. This is necessary because
        # the generator is closed after the first iteration. Moreover we need
        # need, e.g. in the case an exception when storing the data.
        @self._stream_logger
        def stream() -> Generator[T]:
            yield from get_new_stream()

        self._store.db.add_all_from_iterable(stream(), True)

    @abstractmethod
    def _get_new_stream(self) -> Callable[..., Generator[T]]:
        """
        Returns a new dispatcher for initializing a new data stream
        """

    def _get_new_stream_from_dispatcher(
        self, dispatcher: StreamDispatcher[V]
    ) -> Callable[..., Generator[T]]:
        """
        Returns a callable that returns a new stream from the dispatcher.
        """

        return dispatcher.subscribe(self._process_item_stream, self._new_recon_source)

    def _get_new_stream_from_paginator(
        self,
        get_stream: Callable[[int], Generator[V]],
        get_offset: Callable[..., int],
    ) -> Callable[..., Generator[T]]:
        """
        Returns a callable that returns a new stream from a paginator.
        """

        def new_stream() -> Generator[T]:
            source = get_stream(get_offset())
            converter = StreamConverter(
                source, lambda item: self._process_item_stream(item, False)
            )
            yield from converter.items()

        return new_stream

    @abstractmethod
    def _new_recon_source(self) -> StreamConverter[T, U] | None:
        """
        Returns a new reconciliation source.
        """

    def start(self) -> None:

        self._start_stream(self._get_new_stream())

    def pre_sync_start(self) -> None:
        """
        Presync items from the LND API. This is done before the
        subscription starts. This is necessary to get the items that were
        made while the subscription was not running.
        """

        msg = f"Presync {self._items_name} for {self._pub_key}"
        self._logger.info(f"{msg}...")

        # Delete orphaned objects in sync mode. This is necessary here to avoid
        # foreign key constraints violations when adding new objects.
        self._delete_orphaned_data()
        self._pre_sync_start()

        self._logger.info(f"{msg} finished")
