from collections.abc import Callable, Generator

from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.tracker.lnd import LndBaseReconSource, LndBaseTracker
from feelancer.tracker.models import ForwardingEvent
from feelancer.utils import ns_to_datetime

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds
PAGINATOR_BLOCKING_INTERVAL = 21  # 21 seconds
CHUNK_SIZE = 1000

type LndForwardReconSource = LndBaseReconSource[ForwardingEvent, ln.ForwardingEvent]


class LNDFwdTracker(LndBaseTracker):

    def _delete_orphaned_data(self) -> None:
        return None

    def _get_items_name(self) -> str:
        return "forwards"

    def _pre_sync_source(self) -> LndForwardReconSource:
        index_offset = self._store.get_count_forwarding_events()
        self._logger.debug(f"Starting from index {index_offset} for {self._pub_key}")

        paginator = self._lnd.paginate_forwarding_events(index_offset=index_offset)

        return LndBaseReconSource(paginator, self._process_forwarding_event, False)

    def _process_item_stream(
        self,
        item: ln.ForwardingEvent,
        recon_running: bool,
    ) -> Generator[ForwardingEvent]:

        return self._process_forwarding_event(item, recon_running)

    def _process_item_pre_sync(
        self,
        item: ln.ForwardingEvent,
        recon_running: bool,
    ) -> Generator[ForwardingEvent]:

        return self._process_forwarding_event(item, recon_running)

    def _new_recon_source(self) -> None:
        return None

    def _get_new_stream(self) -> Callable[..., Generator[ForwardingEvent]]:

        return self._get_new_stream_from_paginator(
            lambda offset: self._lnd.paginate_forwarding_events(
                index_offset=offset, blocking_sec=PAGINATOR_BLOCKING_INTERVAL
            ),
            self._store.get_count_forwarding_events,
        )

    def _process_forwarding_event(
        self, fwd: ln.ForwardingEvent, recon_running: bool
    ) -> Generator[ForwardingEvent]:

        yield ForwardingEvent(
            ln_node_id=self._store.ln_node_id,
            timestamp=ns_to_datetime(fwd.timestamp_ns),
            chan_id_in=fwd.chan_id_in,
            chan_id_out=fwd.chan_id_out,
            fee_msat=fwd.fee_msat,
            amt_in_msat=fwd.amt_in_msat,
            amt_out_msat=fwd.amt_out_msat,
        )
