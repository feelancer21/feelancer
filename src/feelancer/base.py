import os
import signal
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Protocol, TypeVar

from feelancer.log import getLogger

from .event import stop_event

T = TypeVar("T")


class Server(Protocol):
    """
    A Server is a service running as a daemon and have to be started and stopped.
    """

    def start(self) -> None: ...
    def stop(self) -> None: ...


def _run_concurrent(
    tasks: list[Callable[[], None]], err_signal: signal.Signals | None
) -> None:
    """
    Starts the provided tasks concurrently. If an error is raised by one task,
    we send a signal to the signal handler to stop the MainServer.
    """

    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(t): t for t in tasks}

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                if err_signal is not None:
                    # Context can only be left after all threads have finished
                    # hence we send a signal to the signal handler to stop the
                    # MainServer.
                    os.kill(os.getpid(), err_signal)
                executor.shutdown(wait=True, cancel_futures=True)
                raise e


def _run_sync(tasks: list[Callable[[], None]]) -> None:
    """Runs the tasks synchronously."""

    for task in tasks:
        task()


class BaseServer:
    """
    Base class for servers.
    """

    def __init__(self) -> None:

        # Logger for the server
        self._logger = getLogger(self.__module__)

        # Callables to be called synchronously during server start
        self._sync_start: list[Callable[[], None]] = []

        # Callables to be called synchronously during server stop
        self._sync_stop: list[Callable[[], None]] = []

        # Callables to be started during server start concurrently
        self._concurrent_start: list[Callable[[], None]] = []

        # Callables to be started during server stop concurrently
        self._concurrent_stop: list[Callable[[], None]] = []

    def _register_sub_server(self, subserver: Server) -> None:

        self._register_starter(subserver.start)
        self._register_stopper(subserver.stop)

    def _register_sync_starter(self, starter: Callable[[], None]) -> None:
        self._sync_start.append(starter)

    def _register_sync_stopper(self, stopper: Callable[[], None]) -> None:
        self._sync_stop.append(stopper)

    def _register_starter(self, starter: Callable[[], None]) -> None:
        self._concurrent_start.append(starter)

    def _register_stopper(self, stopper: Callable[[], None]) -> None:
        self._concurrent_stop.append(stopper)

    def start(self) -> None:
        """
        Starts the server using concurrent futures.
        If an error is raised by one thread, the stop method of the server is called.
        """

        # Preventing start if stop occurred before start. It's more a workaround
        # and safe in all cases.
        if stop_event.is_set():
            return

        # If an error occurs, a SIGTERM signal is sent to the signal handler
        # to stop the server.
        # The signal handler stops all sub servers of the main server.

        err_signal = signal.SIGTERM
        try:
            self._logger.info("Starting...")
            _run_sync(self._sync_start)

            if not stop_event.is_set():
                _run_concurrent(self._concurrent_start, err_signal)
            self._logger.info("Finished")

        except Exception:
            self._logger.error("During start: an unexpected error occurred: {e}")
            self._logger.exception("Start exception:\n")

            # Sending a SIGTERM signal to delegate the graceful shutdown to the
            # signal handler.
            os.kill(os.getpid(), err_signal)

    def stop(self) -> None:
        """
        Stops the server.
        """

        try:
            self._logger.info("Stopping...")
            _run_sync(self._sync_stop)
            _run_concurrent(self._concurrent_stop, None)
            self._logger.info("Stopped.")
        except Exception as e:
            self._logger.error(f"During stop: an unexpected error occurred: {e}")
            self._logger.exception("Stop exception:\n")

            # Waiting for timeout of the signal handler now
