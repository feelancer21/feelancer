# module with a centralized stop event object.

import threading

stop_event = threading.Event()


def stop_retry():
    stop_event.set()
