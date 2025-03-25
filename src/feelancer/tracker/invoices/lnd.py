import datetime
import logging
from collections.abc import Callable, Generator

import pytz

from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.client import LndGrpc
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.log import stream_logger
from feelancer.retry import default_retry_handler
from feelancer.tracker.data import InvoiceNotFound, TrackerStore
from feelancer.tracker.models import (
    Invoice,
    InvoiceHTLC,
    InvoiceHTLCResolveInfo,
    InvoiceHTLCState,
)
from feelancer.utils import bytes_to_str, sec_to_datetime

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds
CHUNK_SIZE = 1000

logger = logging.getLogger(__name__)
invoice_stream_logger = stream_logger(interval=100, items_name="invoices")


class LNDInvoiceReconSource:
    def __init__(
        self,
        lnd: LndGrpc,
        process_payment: Callable[[ln.Invoice, bool], Generator[Invoice]],
    ):
        self._process_invoices = process_payment

        recon_start = datetime.datetime.now(tz=pytz.utc) - datetime.timedelta(
            seconds=RECON_TIME_INTERVAL
        )
        self._paginator = lnd.paginate_invoices(
            creation_date_start=int(recon_start.timestamp()),
        )
        self._is_stopped = False

    def items(self) -> Generator[Invoice]:
        for i in self._paginator:
            yield from self._process_invoices(i, True)

            if self._is_stopped:
                self._paginator.close()

    def stop(self) -> None:
        self._is_stopped = True


class LNDInvoiceTracker:

    def __init__(self, lnd: LNDClient, store: TrackerStore):

        self._lnd: LndGrpc = lnd.lnd
        self._pub_key = lnd.pubkey_local
        self._store = store
        self._is_stopped = False

    def start(self) -> None:

        def get_recon_source() -> LNDInvoiceReconSource:
            return LNDInvoiceReconSource(self._lnd, self._process_invoice)

        dispatcher = self._lnd.subscribe_invoices_dispatcher
        start_stream = dispatcher.subscribe(self._process_invoice, get_recon_source)

        self._store_invoices(start_stream)

    def pre_sync_start(self) -> None:

        logger.info(f"Presync invoices for {self._pub_key}...")

        self._pre_sync_start()
        logger.info(f"Presync invoices for {self._pub_key} finished")

    def pre_sync_stop(self) -> None:
        """
        Stops the presync process.
        """
        self._is_stopped = True

    @default_retry_handler
    def _pre_sync_start(self) -> None:
        """
        Starts the presync process with a retry handler.
        """

        if self._is_stopped:
            return

        self._store.db.add_chunks_from_iterable(
            self._attempts_from_paginator(), chunk_size=CHUNK_SIZE
        )

    @invoice_stream_logger
    def _attempts_from_paginator(self) -> Generator[Invoice]:
        """
        Processes all invoices from the paginator.
        """

        index_offset = self._store.get_max_invoice_add_index()
        logger.debug(f"Starting from index {index_offset} for {self._pub_key}")

        for i in self._lnd.paginate_invoices(index_offset=index_offset):
            if self._is_stopped:
                return

            yield from self._process_invoice(i, False)

    @default_retry_handler
    def _store_invoices(self, start_stream: Callable[..., Generator[Invoice]]) -> None:

        @invoice_stream_logger
        def attempts_from_stream() -> Generator[Invoice]:
            yield from start_stream()

        self._store.db.add_all_from_iterable(attempts_from_stream())

    def _process_invoice(
        self, i: ln.Invoice, recon_running: bool
    ) -> Generator[Invoice]:
        """
        Processes a single invoice.
        """

        # if the invoice is not settled, we do not need to process it
        if i.state != 1:
            return

        if recon_running:
            try:
                # if the invoice is already in the database, we do not need to
                # process it
                self._store.get_invoice_id(bytes_to_str(i.r_hash))
                return None
            except InvoiceNotFound:
                logger.debug(
                    f"Invoice reconciliation: {i.add_index=}; {i.settle_index=} not found."
                )
                pass

        invoice = self._create_invoice(i)

        for h in i.htlcs:
            self._create_htlc(h, invoice)

        yield invoice

    def _create_invoice(self, invoice: ln.Invoice) -> Invoice:
        """
        Creates an invoice object.
        """

        return Invoice(
            ln_node_id=self._store.ln_node_id,
            r_hash=bytes_to_str(invoice.r_hash),
            creation_time=sec_to_datetime(invoice.creation_date),
            value_msat=invoice.value_msat,
            add_index=invoice.add_index,
            settle_index=invoice.settle_index,
        )

    def _create_htlc(self, htlc: ln.InvoiceHTLC, invoice: Invoice) -> InvoiceHTLC:

        resolve_info = InvoiceHTLCResolveInfo(
            resolve_time=sec_to_datetime(htlc.resolve_time),
            state=InvoiceHTLCState(htlc.state),
        )

        return InvoiceHTLC(
            invoice=invoice,
            resolve_info=resolve_info,
            amt_msat=htlc.amt_msat,
            accept_time=sec_to_datetime(htlc.accept_time),
            expiry_height=htlc.expiry_height,
        )
