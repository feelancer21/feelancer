from collections.abc import Callable, Iterable
from typing import Generic, TypeVar

from sqlalchemy import Delete, Select

from feelancer.base import BaseServer
from feelancer.tracker.tracker import Tracker

T = TypeVar("T")


class TrackerBaseService(Generic[T], BaseServer):
    """
    A base class for a services using the Tracker protocol
    """

    def __init__(
        self,
        tracker: Tracker,
        get_config: Callable[..., T | None],
        db_to_csv: Callable[[Select[tuple], str, list[str] | None], None],
        db_delete_data: Callable[[Iterable[Delete[tuple]]], None],
        **kwargs,
    ) -> None:

        super().__init__(**kwargs)
        self.tracker = tracker
        self._get_config = get_config
        self._db_to_csv = db_to_csv
        self._db_delete_data = db_delete_data

        self._register_sync_starter(self._start_server)
        self._register_sync_stopper(self._stop_server)

    def _start_server(self) -> None:
        """Start storing new items."""

        self.tracker.start()

    def _stop_server(self) -> None:
        # Service ends when the incoming data stream has exhausted.
        # But we have to stop the pre sync with is started synchronously.

        self.tracker.pre_sync_stop()
        return None
