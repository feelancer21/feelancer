import logging
import os
import tempfile
import threading
import time

import toml
from lnqueue import LnQueueClient, LnQueues

from feelancer.lightning.client import Channel, ChannelPolicy, LightningClient
from feelancer.log import DEFAULT_LOG_FORMAT
from feelancer.tasks.runner import TaskRunner
from feelancer.utils import SignalHandler, read_config_file


def _new_tmp_file(suffix: str) -> str:
    """Creates a new temporary file and returns its name."""
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_file_name = temp_file.name
    temp_file.close()

    return temp_file_name


class QueueRunner(TaskRunner):
    """
    QueueRunner is a TaskRunner which uses as LightningClient a LnQueueClient.
    """

    def __init__(self, config_file: str, pubkey_local: str, queues: LnQueues):

        def set_lnclient(self) -> None:
            ln: LightningClient = LnQueueClient(pubkey_local, queues)
            self.lnclient = ln

        # Monkey patch of the _set_lnclient function to avoid that a grpc client
        # is initiated during super.__init__.
        TaskRunner._set_lnclient = set_lnclient
        super().__init__(config_file)


class TestSetup:
    """
    The test environment which initializes and holds the queue runner, the queues
    signal handler and the config.
    """

    def __init__(self, pubkey_local: str) -> None:
        # Logging to stdout
        logging.basicConfig(
            level=logging.DEBUG,
            format=DEFAULT_LOG_FORMAT,
        )

        # We are reading the original config file name from environment. The
        # config is read into a dictionary self.config.
        # During the tests we want to test different config scenarios. Because
        # there is no API until now, the user has the tester has possibility
        # to change the config and write the changes to a tmp config file.
        # The runner is initiated with this tmp config file.
        config_file = os.environ.get("FEELANCER_CONFIG")
        if not config_file:
            raise ValueError("env variable 'FILE_CONFIG' not provided")

        self.config = read_config_file(config_file)
        self.tmp_file = _new_tmp_file(".toml")

        # Clean up of the tmp file.
        def remove_tmp():
            if os.path.exists(self.tmp_file):
                os.remove(self.tmp_file)

        # Creating a signal handler which is used for premature exit, removing
        # the tmp file and stopping the runner later.
        self.sig_handler = SignalHandler()
        self.sig_handler.exit_on_signal(True, "Feelancer itests aborted.\n")
        self.sig_handler.add_handler(remove_tmp)

        logging.debug(f"temporary config file is {self.tmp_file}")

        # Writing of the config to the tmp file.
        self.write_tmp_config()

        # Init of the runner Queues and the runner.
        self.queues = LnQueues()
        self.runner = QueueRunner(self.tmp_file, pubkey_local, self.queues)

        self.sig_handler.add_handler(self.runner.stop)

    def write_tmp_config(self) -> None:
        """Writes the config as toml format to the created tmp file."""

        with open(self.tmp_file, "w") as file:
            toml.dump(self.config, file)


if __name__ == "__main__":
    """
    Proof of Concept for playing around with the test clients. No real tests.
    """

    s = TestSetup("mynode")

    s.queues.block_height.put(1_000_000)

    local_policy = ChannelPolicy(
        fee_rate_ppm=1000,
        base_fee_msat=100,
        time_lock_delta=144,
        min_htlc_msat=1_000,
        max_htlc_msat=10_000_000,
        inbound_fee_rate_ppm=-100,
        inbound_base_fee_msat=-50,
        disabled=False,
        last_update=0,
    )
    chan = Channel(
        chan_id=916676038404210688,
        chan_point="alice_chan",
        pub_key="alice",
        private=False,
        opening_height=1,
        capacity_sat=10_000_000,
        liquidity_out_settled_sat=8_000_000,
        liquidity_out_pending_sat=500_000,
        liquidity_in_settled_sat=1_000_000,
        liquidity_in_pending_sat=500_000,
        policy_local=local_policy,
        policy_remote=None,
    )
    s.queues.channels.put({123: chan})

    runner_thread = threading.Thread(target=s.runner.start)
    runner_thread.start()
    logging.info("thread started")
    time.sleep(45)

    # Calling the handlers to stop the scheduler and remove the tmp file.
    s.sig_handler.call_handlers()

    # Waiting that all threads have finished.
    runner_thread.join()
    logging.info("thread joined")
    logging.info("Feelancer itests successfully finished.\n")
