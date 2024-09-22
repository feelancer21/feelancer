from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .log import set_logger
from .tasks.runner import TaskRunner
from .utils import read_config_file


def _get_args():
    parser = argparse.ArgumentParser(
        prog="feelancer",
        description="Adjusting fees for Lightning Channels using a PID-Controller",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Input file for reading (default: '~/.feelancer/feelancer.toml')",
        default="~/.feelancer/feelancer.toml",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show the version",
    )
    return parser.parse_args()


def app():
    try:
        args = _get_args()
        if args.version:
            print(f"Version: {__version__}")
            sys.exit(0)

        config_file = args.config
        config = read_config_file(config_file)

        set_logger(config.get("logging"))
        logging.info("Feelancer starting")

        runner = TaskRunner(config_file)

        runner.start()

    except Exception as e:
        logging.exception("An unexpected error occurred")
        raise e

    finally:
        logging.info("Feelancer shutdown completed\n")


if __name__ == "__main__":
    app()
