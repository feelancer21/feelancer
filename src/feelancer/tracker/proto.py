from collections.abc import Callable, Iterable
from typing import Generic, Protocol, TypeVar

from sqlalchemy import Delete, Select

from feelancer.tasks.runner import RunnerRequest, RunnerResult

T = TypeVar("T", covariant=True)


class Tracker(Protocol):

    def start(self) -> None:
        """
        Fetches the latest data from an incoming data stream and updates the database.
        """
        ...

    def pre_sync_start(self) -> None:
        """
        Ability to the synchronize the data before the actual start of the tracker
        """
        ...

    def pre_sync_stop(self) -> None:
        """
        Stops the pre sync process gracefully.
        """
        ...


class TrackerBaseService(Generic[T]):
    """
    A base class for a services using the Tracker protocol
    """

    def __init__(
        self,
        get_config: Callable[..., T | None],
        db_to_csv: Callable[[Select[tuple], str, list[str] | None], None],
        db_delete_data: Callable[[Iterable[Delete[tuple]]], None],
    ) -> None:

        self._get_config = get_config
        self._db_to_csv = db_to_csv
        self._db_delete_data = db_delete_data


class TrackerService(Generic[T], Protocol):

    def __init__(
        self,
        get_config: Callable[..., T | None],
        db_to_csv: Callable[[Select[tuple], str, list[str] | None], None],
        db_delete_data: Callable[[Iterable[Delete[tuple]]], None],
    ) -> None: ...

    def run(self, request: RunnerRequest) -> RunnerResult: ...
