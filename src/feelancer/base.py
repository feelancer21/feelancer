import logging
import os
import signal
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from functools import wraps
from typing import Protocol, TypeVar

import pytz

DEFAULT_EXCEPTIONS_RETRY = (Exception,)
DEFAULT_EXCEPTIONS_RAISE = ()
DEFAULT_MAX_RETRIES = 5
DEFAULT_DELAY = 300  # 5 minutes
DEFAULT_MIN_TOLERANCE_DELTA = 900  # 15 minutes

T = TypeVar("T")


class Server(Protocol):
    """
    A Server is a service running as a daemon and have to be started and stopped.
    """

    def start(self) -> None: ...
    def stop(self) -> None: ...


def _run_concurrent(
    tasks: list[Callable[..., None]], err_signal: signal.Signals | None
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


def _run_sync(tasks: list[Callable[..., None]]) -> None:
    """Runs the tasks synchronously."""

    for task in tasks:
        task()


def create_retry_handler(
    exceptions_retry: tuple[type[Exception], ...],
    exceptions_raise: tuple[type[Exception], ...],
    max_retries: int,
    delay: int,
    min_tolerance_delta: int | None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Retries the function max_retries times if the function returned None.
    If a delay is given, the function waits for the given amount of seconds
    before retrying.
    """

    def retry_handler(func: Callable[..., T]) -> Callable[..., T]:

        @wraps(func)
        def wrapper(*args, **kwargs):

            retries_left = max_retries
            while True:

                if min_tolerance_delta is not None:
                    min_tolerence_time = datetime.now(pytz.utc) + timedelta(
                        seconds=min_tolerance_delta
                    )
                else:
                    min_tolerence_time = None

                try:
                    return func(*args, **kwargs)

                except exceptions_raise as e:
                    logging.error(f"An error occurred: {e}")
                    raise e

                except exceptions_retry as e:
                    logging.error(f"An error occurred: {e}")
                    if (
                        min_tolerence_time is not None
                        and datetime.now(pytz.utc) > min_tolerence_time
                    ):
                        retries_left = max_retries

                    if retries_left == 0:
                        raise e

                    logging.debug(f"{retries_left=}")
                    retries_left -= 1
                    if delay > 0:
                        logging.debug(f"Waiting {delay}s before retrying...")
                        time.sleep(delay)

        return wrapper

    return retry_handler


default_retry_handler = create_retry_handler(
    exceptions_raise=DEFAULT_EXCEPTIONS_RAISE,
    exceptions_retry=DEFAULT_EXCEPTIONS_RETRY,
    max_retries=DEFAULT_MAX_RETRIES,
    delay=DEFAULT_DELAY,
    min_tolerance_delta=DEFAULT_MIN_TOLERANCE_DELTA,
)


class BaseServer:
    """
    Base class for servers.
    """

    def __init__(self) -> None:

        # Name of the server
        self._name: str = f"[{self.__class__.__name__}]"

        # Callables to be called synchronously during server start
        self._sync_start: list[Callable[..., None]] = []

        # Callables to be called synchronously during server stop
        self._sync_stop: list[Callable[..., None]] = []

        # Callables to be started during server start concurrently
        self._concurrent_start: list[Callable[..., None]] = []

        # Callables to be started during server stop concurrently
        self._concurrent_stop: list[Callable[..., None]] = []

        # Flag to indicate if the server is stopped
        self._is_stopped: bool = False

    def _register_sub_server(self, subserver: Server) -> None:

        self._register_starter(subserver.start)
        self._register_stopper(subserver.stop)

    def _register_sync_starter(self, starter: Callable[..., None]) -> None:
        self._sync_start.append(starter)

    def _register_sync_stopper(self, stopper: Callable[..., None]) -> None:
        self._sync_stop.append(stopper)

    def _register_starter(self, starter: Callable[..., None]) -> None:
        self._concurrent_start.append(starter)

    def _register_stopper(self, stopper: Callable[..., None]) -> None:
        self._concurrent_stop.append(stopper)

    def start(self) -> None:
        """
        Starts the server using concurrent futures.
        If an error is raised by one thread, the stop method of the server is called.
        """

        # If an error occurs, a SIGTERM signal is sent to the signal handler
        # to stop the server.
        # The signal handler stops all sub servers of the main server.

        err_signal = signal.SIGTERM
        try:
            logging.info(f"{self._name} starting...")
            _run_sync(self._sync_start)
            _run_concurrent(self._concurrent_start, err_signal)
            logging.info(f"{self._name} finished")
        except Exception as e:
            logging.error(f"{self._name} start: an unexpected error occurred: {e}")
            logging.exception(f"{self._name} start exception")

            # Sending a SIGTERM signal to delegate the graceful shutdown to the
            # signal handler.
            os.kill(os.getpid(), err_signal)

    def stop(self) -> None:
        """
        Stops the server.
        """

        self._is_stopped = True

        try:
            logging.info(f"{self._name} stopping...")
            _run_sync(self._sync_stop)
            _run_concurrent(self._concurrent_stop, None)
            logging.info(f"{self._name} stopped.")
        except Exception as e:
            logging.error(f"{self._name} stop: an unexpected error occurred: {e}")
            logging.exception(f"{self._name} stop exception")

            # Waiting for timeout of the signal handler now
