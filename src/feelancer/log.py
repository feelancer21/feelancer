from __future__ import annotations

import logging

DEFAULT_LOG_FILE = "feelancer.log"
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s]: %(message)s"


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
