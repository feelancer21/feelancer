from __future__ import annotations

import logging

DEFAULT_LOG_FILE = "feelancer.log"
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s]: %(message)s"


def _get_log_level(config: dict | None):
    if config is None:
        return DEFAULT_LOG_LEVEL

    level_str = config.get("level")
    if level_str == "DEBUG":
        return logging.DEBUG
    elif level_str == "INFO":
        return logging.INFO
    elif level_str == "WARNING":
        return logging.WARNING
    elif level_str == "ERROR":
        return logging.ERROR
    elif level_str == "CRITICAL":
        return logging.CRITICAL

    return DEFAULT_LOG_LEVEL


def _get_log_file(config: dict | None):
    if config is None:
        return DEFAULT_LOG_FILE

    logfile = config.get("logfile")
    if logfile is None:
        return DEFAULT_LOG_FILE

    return logfile


def set_logger(config: dict | None):
    loglevel = _get_log_level(config)
    logfile = _get_log_file(config)

    logging.basicConfig(
        level=loglevel,
        format=DEFAULT_LOG_FORMAT,
        handlers=[logging.FileHandler(logfile)],
    )
