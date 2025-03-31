import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from typing import Generic, TypeVar

from google.protobuf.message import Message
from sqlalchemy.orm import DeclarativeBase

from feelancer.event import stop_event
from feelancer.grpc.client import StreamDispatcher
from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.client import LndGrpc
from feelancer.log import stream_logger
from feelancer.retry import default_retry_handler

from .data import TrackerStore

# ORM objects to be stored in the database
T = TypeVar("T", bound=DeclarativeBase)
# grpc Message returned by lnd during the pre sync and the recon
# (e.g. ListPaymentsResponse)
U = TypeVar("U", bound=Message)
# grpc Message returned by lnd data stream (e.g. TrackPayments)
V = TypeVar("V", bound=Message)

CHUNK_SIZE = 1000
STREAM_LOGGER_INTERVAL = 100


class LndBaseReconSource(Generic[T, U]):
    def __init__(
        self,
        source_items: Generator[U],
        process_item: Callable[[U, bool], Generator[T]],
    ):
        self._source_items = source_items
        self._process_item = process_item

    def items(self) -> Generator[T]:
        for item in self._source_items:
            yield from self._process_item(item, True)

            if stop_event.is_set():
                self._source_items.close()


class LndBaseTracker(Generic[T, U, V], ABC):
    def __init__(self, lnd: LNDClient, store: TrackerStore):

        self._lnd: LndGrpc = lnd.lnd
        self._pub_key = lnd.pubkey_local
        self._store = store
        self._items_name = self._get_items_name()
        self._logger = logging.getLogger(self.__module__)
        self._stream_logger = stream_logger(
            interval=STREAM_LOGGER_INTERVAL,
            items_name=self._items_name,
            logger=self._logger,
        )

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
    def _pre_sync_source(self) -> Generator[U]:
        """
        Source of items to be processed in the presync process.
        """

    @abstractmethod
    def _process_item_stream(self, item: V, recon_running: bool) -> Generator[T]:
        """
        Process an item from the LND API to a SQLAlchemy object.
        """

    @abstractmethod
    def _process_item_pre_sync(self, item: U, recon_running: bool) -> Generator[T]:
        """
        Process an item from the LND API to a SQLAlchemy object.
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
            stream = self._pre_sync_source()
            for item in stream:
                if stop_event.is_set():
                    stream.close()

                yield from self._process_item_pre_sync(item, recon_running=False)

        self._store.db.add_chunks_from_iterable(
            pre_sync_stream(), chunk_size=CHUNK_SIZE
        )

    @default_retry_handler
    def _start_stream(self, get_new_stream: Callable[..., Generator[T]]) -> None:
        """
        Fetches the items from the subscription and stores them in the database.
        """

        # In every retry we initialize a new generator. This is necessary because
        # the generator is closed after the first iteration. Moreover we need
        # need, e.g. in the case an exception when storing the data.
        @self._stream_logger
        def stream() -> Generator[T]:
            yield from get_new_stream()

        self._store.db.add_all_from_iterable(stream(), True)

    @abstractmethod
    def _new_dispatcher(self) -> StreamDispatcher[V]:
        """
        Returns a new dispatcher for initializing a new data stream
        """

    @abstractmethod
    def _new_recon_source(self) -> LndBaseReconSource[T, U] | None:
        """
        Returns a new reconciliation source.
        """

    def start(self) -> None:

        dispatcher: StreamDispatcher[V] = self._new_dispatcher()

        get_new_stream = dispatcher.subscribe(
            self._process_item_stream, self._new_recon_source
        )
        self._start_stream(get_new_stream)

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
