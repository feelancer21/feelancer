from __future__ import annotations

import functools
import logging

DEFAULT_LOG_FILE = "feelancer.log"
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"


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
        logger = logging.getLogger(func.__module__)
        logger.debug(f"Calling {func.__name__}, args: {args=}, kwargs: {kwargs=}")
        return func(*args, **kwargs)

    return wrapper
