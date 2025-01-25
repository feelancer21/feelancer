from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .log import set_logger
from .tasks.runner import TaskRunner
from .utils import SignalHandler, read_config_file


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
        logging.info(f"Feelancer {__version__=} starting...")

        runner = TaskRunner(config_file)

        # sig_handlers executes callables when SIGTERM or SIGINT is received.
        sig_handler = SignalHandler()

        # Stopping the runner when signal is received.
        sig_handler.add_handler(runner.stop)

        runner.start()

    except Exception as e:
        logging.exception("An unexpected error occurred.")
        raise e

    finally:
        logging.info("Feelancer shutdown completed.\n")


if __name__ == "__main__":
    app()
