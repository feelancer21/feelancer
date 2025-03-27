import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import wraps

import pytz

from .event import stop_event

DEFAULT_EXCEPTIONS_RETRY = (Exception,)
DEFAULT_EXCEPTIONS_RAISE = ()
DEFAULT_MAX_RETRIES = 5
DEFAULT_DELAY = 300  # 5 minutes
DEFAULT_MIN_TOLERANCE_DELTA = 900  # 15 minutes


def create_retry_handler(
    exceptions_retry: tuple[type[Exception], ...],
    exceptions_raise: tuple[type[Exception], ...],
    max_retries: int,
    delay: int,
    min_tolerance_delta: int | None,
) -> Callable:
    """
    Retries the function max_retries times if the function returned None.
    If a delay is given, the function waits for the given amount of seconds
    before retrying.
    """

    def retry_handler(func):
        logger: logging.Logger = logging.getLogger(func.__module__)

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
                    raise e

                except exceptions_retry as e:
                    logger.error(f"An error occurred: {e}; Check {retries_left=}")
                    if (
                        min_tolerence_time is not None
                        and datetime.now(pytz.utc) > min_tolerence_time
                    ):
                        retries_left = max_retries

                    if retries_left == 0:
                        raise e

                    logger.debug(f"{retries_left=}")
                    retries_left -= 1
                    if delay > 0:
                        logger.debug(f"Waiting {delay}s before retrying...")

                        # Wait for the delay seconds before retrying.
                        stop_event.wait(delay)

                        # If the server was stopped we stop retrying.
                        if stop_event.is_set():
                            return None

        return wrapper

    return retry_handler


default_retry_handler = create_retry_handler(
    exceptions_raise=DEFAULT_EXCEPTIONS_RAISE,
    exceptions_retry=DEFAULT_EXCEPTIONS_RETRY,
    max_retries=DEFAULT_MAX_RETRIES,
    delay=DEFAULT_DELAY,
    min_tolerance_delta=DEFAULT_MIN_TOLERANCE_DELTA,
)
