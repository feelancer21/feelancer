# import logging
# import os
# import tempfile
# import threading
# import time
# from datetime import datetime, timedelta

# import pytz
# import toml
# from lnqueue import LnQueueClient, LnQueues

# from feelancer.config import FeelancerConfig
# from feelancer.data.db import FeelancerDB
# from feelancer.lightning.client import Channel, ChannelPolicy, LightningClient
# from feelancer.log import DEFAULT_LOG_FORMAT
# from feelancer.tasks.runner import TaskRunner
# from feelancer.utils import SignalHandler, read_config_file


# def _new_tmp_file(suffix: str) -> str:
#     """Creates a new temporary file and returns its name."""
#     temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
#     temp_file_name = temp_file.name
#     temp_file.close()

#     return temp_file_name


# class QueueRunner(TaskRunner):
#     """
#     QueueRunner is a TaskRunner which uses as LightningClient a LnQueueClient.
#     """

#     def __init__(self, config_file: str, pubkey_local: str, queues: LnQueues):

#         config_dict = read_config_file(config_file)
#         if "sqlalchemy" in config_dict:
#             db = FeelancerDB.from_config_dict(config_dict["sqlalchemy"]["url"])
#         else:
#             raise ValueError("'sqlalchemy' section is not included in config-file")

#         ln: LightningClient = LnQueueClient(pubkey_local, queues)
#         self.lnclient = ln

#         # Quick and dirty
#         super().__init__(ln, db, 21, 5, lambda s: FeelancerConfig(config_dict))


# class TestSetup:
#     """
#     The test environment which initializes and holds the queue runner, the queues
#     signal handler and the config.
#     """

#     def __init__(self, pubkey_local: str) -> None:
#         # Logging to stdout
#         logging.basicConfig(
#             level=logging.DEBUG,
#             format=DEFAULT_LOG_FORMAT,
#         )

#         # We are reading the original config file name from environment. The
#         # config is read into a dictionary self.config.
#         # During the tests we want to test different config scenarios. Because
#         # there is no API until now, the user has the tester has possibility
#         # to change the config and write the changes to a tmp config file.
#         # The runner is initiated with this tmp config file.
#         config_file = os.environ.get("FEELANCER_CONFIG")
#         if not config_file:
#             raise ValueError("env variable 'FILE_CONFIG' not provided")

#         self.config = read_config_file(config_file)
#         self.tmp_file = _new_tmp_file(".toml")

#         # Clean up of the tmp file.
#         def remove_tmp():
#             if os.path.exists(self.tmp_file):
#                 os.remove(self.tmp_file)

#         # Creating a signal handler which is used for premature exit, removing
#         # the tmp file and stopping the runner later.
#         self.sig_handler = SignalHandler()
#         self.sig_handler.add_handler(remove_tmp)

#         logging.debug(f"temporary config file is {self.tmp_file}")

#         # Writing of the config to the tmp file.
#         self.write_tmp_config()

#         # Init of the runner Queues and the runner.
#         self.queues = LnQueues()
#         self.runner = QueueRunner(self.tmp_file, pubkey_local, self.queues)

#     def cleanup(self) -> None:
#         self.sig_handler.call_handlers()

#     def stop_runner(self) -> None:
#         """
#         Stops the queue runner gracefully.
#         """

#         self.cleanup()
#         self.thread.join()
#         logging.debug("Queue runner stopped.")

#     def start_runner(self) -> None:
#         """
#         Starts the queue runner in a separated thread.
#         """

#         logging.debug("Starting queue runner...")
#         self.sig_handler.add_handler(self.runner.stop)

#         # def exit():
#         #     logging.debug("Queue runner aborted.")
#         #     sys.exit(255)

#         # self.sig_handler.exit_on_signal = exit

#         self.thread = threading.Thread(target=self.runner.start)
#         self.thread.start()
#         logging.debug("Thread started.")

#     def write_tmp_config(self) -> None:
#         """Writes the config as toml format to the created tmp file."""

#         with open(self.tmp_file, "w") as file:
#             toml.dump(self.config, file)


# def _example_add_testdata(lnq: LnQueues) -> None:
#     lnq.block_height.put(1_000_000)

#     local_policy = ChannelPolicy(
#         fee_rate_ppm=1000,
#         base_fee_msat=100,
#         time_lock_delta=144,
#         min_htlc_msat=1_000,
#         max_htlc_msat=10_000_000,
#         inbound_fee_rate_ppm=-100,
#         inbound_base_fee_msat=-50,
#         disabled=False,
#         last_update=0,
#     )
#     chan = Channel(
#         chan_id=916676038404210688,
#         chan_point="alice_chan",
#         pub_key="alice",
#         private=False,
#         opening_height=1,
#         capacity_sat=10_000_000,
#         liquidity_out_settled_sat=8_000_000,
#         liquidity_out_pending_sat=500_000,
#         liquidity_in_settled_sat=1_000_000,
#         liquidity_in_pending_sat=500_000,
#         policy_local=local_policy,
#         policy_remote=None,
#     )
#     lnq.channels.put({123: chan})


# def _example_run_threaded() -> None:
#     """
#     Remark: Just an example how to work with TestSetup in a separate thread. It
#     is not easy to use for runs which need the BlockingScheduler to be triggered,
#     because you need to fake the time. Tested around with faketimelib
#     ut it is quite difficult to ensure that the time is faked
#     everywhere. Hence I decided to take the Scheduler out of the itest and
#     to call TaskRunner._run directly without the Scheduler.
#     """
#     s = TestSetup("mynode")
#     s.start_runner()
#     try:

#         # Doing the actual stuff here
#         _example_add_testdata(s.queues)

#         # Sleep until the tests should have finished
#         time.sleep(25)

#         # Calling the handlers to stop the scheduler and remove the tmp file.
#         s.stop_runner()

#         # Waiting that all threads have finished.
#         logging.info("Feelancer itests successfully finished.\n")
#     except SystemExit:
#         s.thread.join()
#         logging.error("Itest aborted by an user signal.\n")


# def _example_run_pid_synchronous() -> None:
#     """
#     Proof of Concept for testing pid without using the scheduler
#     """
#     s = TestSetup("mynode")
#     _example_add_testdata(s.queues)

#     timestamp = datetime(2021, 1, 1, 0, 0, 0, tzinfo=pytz.utc)
#     dt = timedelta(minutes=15)

#     # Executing the runner three times.
#     s.runner._run(timestamp)

#     timestamp += dt
#     s.runner._run(timestamp)

#     timestamp += dt
#     s.runner._run(timestamp)

#     # Calling the handlers to stop the scheduler and remove the tmp file.
#     s.cleanup()

#     logging.info("Feelancer itests successfully finished.\n")
