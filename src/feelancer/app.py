from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .log import set_logger
from .server import AppConfig, Server
from .utils import SignalHandler

DEFAULT_CONFIG = "~/.feelancer/feelancer.toml"
# If the stop signal is received, the server has 180 seconds to stop.
DEFAULT_TIMEOUT_ON_SIGNAL = 180


def _get_args():
    parser = argparse.ArgumentParser(
        prog="feelancer",
        description="Adjusting fees for Lightning Channels using a PID-Controller",
    )
    parser.add_argument(
        "--config",
        type=str,
        help=f"Input file for reading (default: '{DEFAULT_CONFIG}')",
        default=DEFAULT_CONFIG,
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Do not start the server. Creates database only. (default: False)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help=(
            f"Timeout for the server to stop after receiving a signal. "
            f"(default: {DEFAULT_TIMEOUT_ON_SIGNAL}s)"
        ),
        default=DEFAULT_TIMEOUT_ON_SIGNAL,
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show the version",
    )
    return parser.parse_args()


def app():

    server: Server | None = None

    try:
        args = _get_args()
        if args.version:
            print(f"Version: {__version__}")
            sys.exit(0)

        config_file = args.config
        config = AppConfig.from_config_file(config_file)

        set_logger(config.log_file, config.log_level)
        logging.info(f"Feelancer {__version__=} starting...")

        server = Server(config)

        # sig_handlers executes callables when SIGTERM or SIGINT is received.
        sig_handler = SignalHandler()

        # Stopping the runner when signal is received.
        sig_handler.add_handler(server.stop)
        sig_handler.add_timeout_handler(server.kill, args.timeout)

        if not args.no_server:
            server.start()
        else:
            logging.info(f"Not starting server: {args.no_server=}")

        logging.info("Feelancer shutdown completed.\n")

    except Exception:
        # Hard exit with killing of all threads if there is an unknown error.
        logging.exception("An unexpected error occurred.")

        if server is not None:
            server.kill()


if __name__ == "__main__":
    app()
