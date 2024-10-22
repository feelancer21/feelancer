from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import grpc
import pytz
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_SCHEDULER_STARTED
from apscheduler.schedulers.blocking import BlockingScheduler, Event
from apscheduler.triggers.interval import IntervalTrigger

from feelancer.config import FeelancerConfig
from feelancer.data.db import FeelancerDB
from feelancer.lightning.chan_updates import update_channel_policies
from feelancer.lightning.data import LightningCache, LightningSessionCache
from feelancer.lightning.models import DBRun

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from feelancer.lightning.chan_updates import PolicyProposal
    from feelancer.lightning.client import LightningClient


@dataclass
class RunnerResult:
    store: Callable[[LightningSessionCache], None] | None = None
    proposals: Iterable[PolicyProposal] | None = None


@dataclass
class RunnerRequest:
    timestamp: datetime
    ln: LightningCache


class TaskRunner:
    def __init__(
        self,
        lnclient: LightningClient,
        db: FeelancerDB,
        seconds: int,
        max_listener_attempts: int,
        read_feelancer_cfg: Callable[..., FeelancerConfig],
    ):
        self.lnclient = lnclient
        self.db = db
        self.read_feelancer_cfg = read_feelancer_cfg

        # Empty list of callables representing the callables which we have
        # to call.
        self.tasks: list[Callable[[RunnerRequest], RunnerResult]] = []

        # Empty list of callables which we have to call in an error case.
        self.resets: list[Callable[..., None]] = []

        # Lock to prevent a race between start() and stop(). The stop can only
        # be executed when the scheduler is running. If the start of the
        # scheduler hasn't finished, the runner cannot be stopped.
        self.lock = threading.Lock()

        # Init of a scheduler. Configuration will be done in the start function.
        self.scheduler = BlockingScheduler()

        # Is 'True' if the listener is running.
        self.listener_running: bool = False

        # Lock to check and set listener_running atomic when starting a new listener.
        self.listener_lock = threading.Lock()

        # Counter for attempted listener starts
        self.listener_attempts: int = 0
        self.max_listener_attempts = max_listener_attempts

        # Setting up a scheduler which call self._run in an interval of
        # self.seconds.
        self.seconds = seconds

    def _run(self, timestamp_start: datetime) -> None:
        """
        Running all tasks associated with this task runner.
        """

        ln = LightningCache(self.lnclient)
        request = RunnerRequest(timestamp_start, ln)

        # Update the config again by reading from the filesystem
        config: FeelancerConfig = self.read_feelancer_cfg()

        store_funcs: list[Callable[[LightningSessionCache], None]] = []
        policy_updates: list[PolicyProposal] = []

        try:
            for t in self.tasks:
                task_result = t(request)

                if task_result.store is not None:
                    store_funcs.append(task_result.store)

                if task_result.proposals is not None:
                    policy_updates += task_result.proposals

            logging.info("Finished task execution")

        except Exception as e:
            logging.error("Could not run all tasks")
            raise e

        timestamp_end = datetime.now(pytz.utc)

        # Now we have to send the results to the lightning backend and store the
        # results to database. There is the minimal risk that one of both is down
        # now.
        # We want to store the channel policies at the end of the run too. That's
        # the reason we do it at first.
        try:
            update_channel_policies(
                self.lnclient, policy_updates, config.peer_config, timestamp_end
            )
        except Exception:
            # We log the exception but don't raise it.
            logging.exception("Unexpected error during policy updates occurred")

        # Storing the relevant data in the database by calling the store_funcs
        # with the cached data. We can return early if there is nothing to store.
        if len(store_funcs) == 0:
            return None

        # Callback function for storing the data in the db.
        def store_data(db_session: Session) -> DBRun:
            db_run = DBRun(
                timestamp_start=timestamp_start,
                timestamp_end=timestamp_end,
            )

            ln_session = LightningSessionCache(ln, db_session, db_run)

            for f in store_funcs:
                f(ln_session)

            return db_run

        # Execute the callable. The lambda function returns the run id
        # for logging.
        run_id = self.db.execute_post(store_data, lambda db_run: db_run.id)

        run_time = timestamp_end - timestamp_start
        logging.info(
            f"Run {run_id} successfully finished; start "
            f"{timestamp_start}; end {timestamp_end}; runtime {run_time}."
        )

        # If config.seconds had changed we modify the trigger of the job.
        if config.seconds != self.seconds:
            self.seconds = config.seconds
            logging.info(f"Interval changed; executing tasks every {self.seconds}s now")
            self.job.modify(trigger=IntervalTrigger(seconds=self.seconds))

    def register_task(self, task: Callable[[RunnerRequest], RunnerResult]) -> None:
        """
        Register a new task which has to be executed by the runner.
        """
        self.tasks.append(task)

    def register_reset(self, reset: Callable[..., None]) -> None:
        """
        Register a new reset task which has to be executed in the case of an
        error.
        """
        self.resets.append(reset)

    def _reset(self) -> None:
        """
        Resets all objects if the data could not be saved in the database.
        These objects must be reinitialized during the next run.
        """

        for r in self.resets:
            r()

        logging.debug("Reset of internal objects completed.")

    def start(self) -> None:
        """
        Initializes a BlockingScheduler and starts it.
        """

        self.lock.acquire()

        # Return early if scheduler is already started.
        if self.scheduler.running:
            return None

        logging.info(f"Starting runner and executing tasks every {self.seconds}s")

        # Starts the run and resets objects in the case of an unexpected error,
        # e.g. db loss.
        def listener_wrapper(event: Event) -> None:
            # We check if there is a running listener and return early if this is the
            # case. To be safe we lock the runner.
            with self.listener_lock:
                is_running = self.listener_running
                self.listener_running = True

            self.listener_attempts += 1
            if is_running:
                logging.warning(
                    "There is a running listener which prevents the start of a new one."
                )
                # It acts like a timeout for pending listener jobs.
                if self.listener_attempts > self.max_listener_attempts:
                    logging.error(
                        f"{self.max_listener_attempts=} exceeded. Killing the scheduler..."
                    )
                    self.scheduler.shutdown(wait=False)

                return None

            try:
                self._run(event.scheduled_run_time.astimezone(pytz.utc))  # type: ignore
                self.listener_attempts = 0
            except grpc.RpcError:
                # Rpc Errors are logged before, but the objects has to be reset.
                self._reset()
            except Exception:
                logging.exception("An unexpected error occurred")
                self._reset()
            finally:
                self.listener_running = False

        # We want to start the run with the scheduled_run_time, to avoid
        # problems with broken delta_times.
        # That's why we add a job which actually does nothing and is executed
        # in an interval of self.seconds. After the job is executed the
        # listener_wrapper is called with the event. The wrapper starts the
        # actual run.
        self.job = self.scheduler.add_job(
            lambda: None, IntervalTrigger(seconds=self.seconds)
        )
        self.scheduler.add_listener(listener_wrapper, EVENT_JOB_EXECUTED)

        # We add a callable as listener to release the lock when the scheduler
        # is started.
        def scheduler_started(event) -> None:
            self.lock.release()
            logging.debug("Scheduler started and lock released.")

        self.scheduler.add_listener(scheduler_started, EVENT_SCHEDULER_STARTED)

        logging.info("Scheduler starting...")
        self.scheduler.start()

        # Signal to the caller that the end of the scheduler was not gracefully.
        if self.listener_attempts > self.max_listener_attempts:
            raise Exception(f"{self.max_listener_attempts=} exceeded")

    def stop(self) -> None:
        """
        Stops the BlockingScheduler
        """

        self.lock.acquire()

        # Return early if scheduler is not running.
        if not self.scheduler.running:
            return None

        logging.info("Shutting down the scheduler...")
        self.scheduler.shutdown(wait=True)

        self.lock.release()
        logging.info("Scheduler shutdown completed.")
