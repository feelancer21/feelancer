from typing import Protocol, TypeVar

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
