from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .log import set_logger
from .server import MainConfig, MainServer

DEFAULT_CONFIG = "~/.feelancer/feelancer.toml"
# If the stop signal is received, the server has 180 seconds to stop.

logger = logging.getLogger(__name__)


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
        "--version",
        action="store_true",
        help="Show the version",
    )
    return parser.parse_args()


def app():

    server: MainServer | None = None

    try:
        args = _get_args()
        if args.version:
            print(f"Version: {__version__}")
            sys.exit(0)

        config_file = args.config
        config = MainConfig.from_config_file(config_file)

        set_logger(config.log_file, config.log_level)
        logger.info(f"Feelancer {__version__=} starting...")

        server = MainServer(cfg=config)

        if not args.no_server:
            server.start()
        else:
            logger.info(f"Not starting server: {args.no_server=}")

        # Flsuhing the logger
        logging.shutdown()
        logger.info("Feelancer shutdown completed.\n")

    except Exception:
        logging.shutdown()
        # Hard exit with killing of all threads if there is an unknown error.
        logger.exception("An unexpected error occurred.")

        if server is not None:
            server.kill()


if __name__ == "__main__":
    app()
