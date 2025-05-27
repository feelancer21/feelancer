from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any, cast

# Removed redundant import of getLogger to avoid redefinition error

DEFAULT_LOG_FILE = "feelancer.log"
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"


TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")


class MyLogger(logging.Logger):
    def __init__(self, name: str) -> None:
        super().__init__(name)

    def trace(self, msg, *args, **kwargs):
        if self.isEnabledFor(TRACE_LEVEL):
            self._log(TRACE_LEVEL, msg, args, **kwargs)

    def trace_lazy(self, msg_call: Callable[[], str], *args, **kwargs):
        """
        Log a message lazily, only evaluating the message when the trace log level
        is enabled.
        """
        if self.isEnabledFor(TRACE_LEVEL):
            self._log(TRACE_LEVEL, msg_call(), args, **kwargs)


logging.setLoggerClass(MyLogger)


def getLogger(name: str) -> MyLogger:
    return cast(MyLogger, logging.getLogger(name))


logger = getLogger(__name__)


def _eval_log_level(level: str | None):
    if level is None:
        return DEFAULT_LOG_LEVEL

    if level == "DEBUG":
        return logging.DEBUG
    elif level == "INFO":
        return logging.INFO
    elif level == "WARNING":
        return logging.WARNING
    elif level == "ERROR":
        return logging.ERROR
    elif level == "CRITICAL":
        return logging.CRITICAL
    elif level == "TRACE":
        return TRACE_LEVEL

    return DEFAULT_LOG_LEVEL


def set_logger(logfile: str | None, loglevel: str | None):
    if logfile is None:
        logfile = DEFAULT_LOG_FILE

    logging.basicConfig(
        level=_eval_log_level(loglevel),
        format=DEFAULT_LOG_FORMAT,
        handlers=[logging.FileHandler(logfile)],
    )


def log_func_call(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = getLogger(func.__module__)
        logger.debug(f"Calling {func.__name__}, args: {args=}, kwargs: {kwargs=}")
        return func(*args, **kwargs)

    return wrapper


def stream_logger(
    interval: int,
    items_name: str = "items",
    logger: logging.Logger | None = None,
) -> Callable:
    """
    Decorator for writing a log message in the given interval of yielded items.
    To see the process is still alive.
    """

    def msg(increment: int, count: int) -> str:
        return (
            f"Processed another {increment} {items_name}; "
            f"total processed: {count} {items_name} since startup"
        )

    # count is shared between all instances of the decorated function
    count: int = 0

    def decorator(generator_func):

        @functools.wraps(generator_func)
        def wrapper(*args: Any, **kwargs: Any):
            nonlocal count
            nonlocal logger
            if logger is None:
                logger = getLogger(generator_func.__module__)

            try:
                for item in generator_func(*args, **kwargs):
                    yield item
                    count += 1
                    if count % interval == 0:
                        logger.info(msg(interval, count))
            finally:
                if (res := count % interval) > 0:
                    logger.info(msg(res, count))

        return wrapper

    return decorator
