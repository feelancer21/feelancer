import threading
import time
from queue import Queue
from feelancer.tasks.runner import TaskRunner
from feelancer.lightning.client import Channel, ChannelPolicy, LightningClient
from lnqueue import LnQueueClient, LnQueues
from feelancer.log import DEFAULT_LOG_FORMAT
import logging
import os


class QueueRunner(TaskRunner):
    """
    QueueRunner is a TaskRunner which uses as LightningClient a LnQueueClient.
    """

    def __init__(self, config_file: str, pubkey_local: str, queues: LnQueues):
        def set_client(self) -> None:
            ln: LightningClient = LnQueueClient(pubkey_local, queues)
            self.lnclient = ln

        TaskRunner._set_lnclient = set_client
        super().__init__(config_file)


if __name__ == "__main__":
    """
    Proof of Concept for playing around with the test clients. No real tests.
    """

    # logging to stdout
    logging.basicConfig(
        level=logging.DEBUG,
        format=DEFAULT_LOG_FORMAT,
    )

    config_file = os.environ.get("FILE_CONFIG")
    if not config_file:
        raise ValueError("env variable 'FILE_CONFIG' not provided")

    queues = LnQueues()
    stop_queue = Queue()
    queues.block_height.put(1_000_000)
    runner = QueueRunner(config_file, "mynode", queues)

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
    queues.channels.put({123: chan})

    runner_thread = threading.Thread(target=runner.start)
    runner_thread.start()
    logging.info("thread started")
    time.sleep(22)

    runner.stop()
    runner_thread.join()
    logging.info("thread joined")
