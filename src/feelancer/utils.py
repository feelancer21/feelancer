from __future__ import annotations

import logging
import os
import signal
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, fields
from typing import TypeVar

import tomli


# GenericConf Class für typing only
@dataclass
class GenericConf:
    pass


T = TypeVar("T", bound=GenericConf)
U = TypeVar("U")


def defaults_from_type(
    defaults: type[T], conf: dict | None, exclude: list[str] | None = None
) -> T:
    if conf is None:
        return defaults()

    conf_copy = deepcopy(conf)
    if exclude is not None:
        for key in exclude:
            del conf_copy[key]

    return defaults(**conf_copy)


def defaults_from_instance(
    defaults: T, conf: dict | None, exclude: list[str] | None = None
) -> T:
    if conf is None:
        return defaults

    conf_copy = deepcopy(conf)
    res = deepcopy(defaults)
    if exclude is not None:
        for key in exclude:
            del conf_copy[key]

    field_names = [f.name for f in fields(res)]

    for key, value in conf_copy.items():
        if key not in field_names:
            raise KeyError(f"{key}")
        setattr(res, key, value)

    return res


def get_peers_config(cls: type[T], conf: dict) -> dict[str, T]:
    res: dict[str, T] = {}

    res["default"] = default = defaults_from_type(cls, conf.get("default"))

    for peer in conf.keys() - ["default"]:
        for pub_key in conf[peer]["pubkeys"]:
            res[pub_key] = defaults_from_instance(default, conf[peer], ["pubkeys"])

    return res


def read_config_file(file_name: str) -> dict:
    config_path = os.path.expanduser(file_name)

    if not os.path.exists(config_path):
        raise FileExistsError(f"Config file '{file_name}' does not exist")

    with open(config_path, "rb") as config_file:
        res = tomli.load(config_file)

    return res


class SignalHandler:
    """
    SignalHandler collects callables which have to be executed if SIGTERM or
    SIGINT is received to shutdown the application gracefully.
    """

    def __init__(
        self,
    ) -> None:
        self._handlers: list[Callable[..., None]] = []
        self._timeout_stop: int | None = None

        # If one signal is received, self._call_handlers is called, which is a
        # wrapper around all callables.
        signal.signal(signal.SIGTERM, self._receive_signal)
        signal.signal(signal.SIGINT, self._receive_signal)

    def add_handler(self, handler: Callable[..., None]) -> None:
        """Adds a Callable for execution."""

        self._handlers.append(handler)

    def add_timeout_handler(self, handler: Callable[..., None], timeout: int) -> None:
        """
        Adds a Callable for execution after a timeout if the execution of the
        signal handlers takes too long.
        """

        def raise_timeout(signum, frame):
            logging.error("Timeout reached")
            handler()

        self._timeout_stop = timeout

        # Signal handler for the timeout. It is activated if a SIGTERM or SIGINT
        # is received.
        signal.signal(signal.SIGALRM, raise_timeout)

    def _receive_signal(self, signum, frame) -> None:
        """Calls all added callables."""

        logging.debug(f"Signal received; signum {signum}, frame {frame}.")

        # Dummy handler to avoid the default behavior after another signal.
        def handler(signum, frame):
            pass

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

        # Activate the timeout signal if it is set.
        if self._timeout_stop is not None:
            logging.debug(f"Setting {self._timeout_stop=}")
            signal.alarm(self._timeout_stop)

        self.call_handlers()
        logging.debug("All signal handlers called.")

    def call_handlers(self) -> None:

        for h in self._handlers:
            h()

        # Reset handlers to avoid calling them again
        self._handlers = []


def first_some(value1: U | None, value2: U) -> U:
    """Returns the first value which is not None"""

    return value1 if value1 is not None else value2


def run_concurrent(
    tasks: list[Callable[..., None]],
) -> None:
    """
    Starts the server using concurrent futures.
    If an error is raised by one thread, the stop method of the server is called.
    """

    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(t): t for t in tasks}

        for future in as_completed(futures):
            future.result()  # This will raise an exception if the thread raised one
