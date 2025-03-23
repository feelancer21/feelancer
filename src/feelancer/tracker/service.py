from collections.abc import Callable, Iterable
from typing import Generic, TypeVar

from sqlalchemy import Delete, Select

T = TypeVar("T")


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
