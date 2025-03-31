import datetime
from collections.abc import Generator

import pytz

from feelancer.lnd.client import LndInvoiceDispatcher
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.tracker.data import InvoiceNotFound
from feelancer.tracker.lnd import LndBaseReconSource, LndBaseTracker
from feelancer.tracker.models import (
    Invoice,
    InvoiceHTLC,
    InvoiceHTLCResolveInfo,
    InvoiceHTLCState,
)
from feelancer.utils import bytes_to_str, sec_to_datetime

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds
CHUNK_SIZE = 1000

type LndInvoiceReconSource = LndBaseReconSource[Invoice, ln.Invoice]


class LNDInvoiceTracker(LndBaseTracker):

    def _delete_orphaned_data(self) -> None:
        return None

    def _get_items_name(self) -> str:
        return "invoices"

    def _pre_sync_source(self) -> LndInvoiceReconSource:

        index_offset = self._store.get_max_invoice_add_index()
        self._logger.debug(f"Starting from index {index_offset} for {self._pub_key}")

        paginator = self._lnd.paginate_invoices(index_offset=index_offset)

        return LndBaseReconSource(paginator, self._process_invoice, False)

    def _process_item_stream(
        self,
        item: ln.Invoice,
        recon_running: bool,
    ) -> Generator[Invoice]:

        return self._process_invoice(item, recon_running)

    def _process_item_pre_sync(
        self,
        item: ln.Invoice,
        recon_running: bool,
    ) -> Generator[Invoice]:

        return self._process_invoice(item, recon_running)

    def _new_recon_source(self) -> LndInvoiceReconSource:

        recon_start = datetime.datetime.now(tz=pytz.utc) - datetime.timedelta(
            seconds=RECON_TIME_INTERVAL
        )

        paginator = self._lnd.paginate_invoices(
            creation_date_start=int(recon_start.timestamp()),
        )

        return LndBaseReconSource(paginator, self._process_invoice, True)

    def _new_dispatcher(self) -> LndInvoiceDispatcher:
        return self._lnd.subscribe_invoices_dispatcher

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
                self._logger.debug(
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
