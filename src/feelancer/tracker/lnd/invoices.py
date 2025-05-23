import datetime
from collections.abc import Callable, Generator

import pytz

from feelancer.data.db import GetIdException
from feelancer.grpc.client import StreamConverter
from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.tracker.data import TrackerStore, new_operation_from_htlcs
from feelancer.tracker.models import (
    HtlcDirectionType,
    HtlcInvoice,
    HtlcResolveInfoSettled,
    HtlcResolveType,
    Invoice,
    Operation,
    TransactionResolveInfo,
    TransactionResolveType,
)
from feelancer.utils import bytes_to_str, sec_to_datetime

from .base import LndBaseTracker

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds

type LndInvoiceReconSource = StreamConverter[Operation, ln.Invoice]


class LNDInvoiceTracker(LndBaseTracker):
    def __init__(self, lnd: LNDClient, store: TrackerStore):
        super().__init__(lnd, store)

        # add_index for start of the next recon. This is set during a recon
        # and is the add_index of the first unsettled invoice. If all
        # invoices are settled we use last add_index.
        self._next_recon_index: int = 0

    def _delete_orphaned_data(self) -> None:
        return None

    def _get_items_name(self) -> str:
        return "invoices"

    def _pre_sync_source(self) -> LndInvoiceReconSource:

        index_offset = self._store.get_max_invoice_add_index()
        self._logger.debug(
            f"Starting pre sync from index {index_offset} for {self._pub_key}"
        )

        paginator = self._lnd.paginate_invoices(index_offset=index_offset)

        return StreamConverter(
            paginator, lambda item: self._process_invoice(item, False)
        )

    def _process_item_stream(
        self,
        item: ln.Invoice,
        recon_running: bool,
    ) -> Generator[Operation]:

        return self._process_invoice(item, recon_running)

    def _new_recon_source(self) -> LndInvoiceReconSource:

        self._logger.debug(
            f"Starting recon from index {self._next_recon_index} for {self._pub_key}"
        )

        recon_start = datetime.datetime.now(tz=pytz.utc) - datetime.timedelta(
            seconds=RECON_TIME_INTERVAL
        )

        paginator = self._lnd.paginate_invoices(
            index_offset=self._next_recon_index,
            creation_date_start=int(recon_start.timestamp()),
        )

        unsettled_found: bool = False

        # Closure to update the next_recon_index until we found
        # a unsettled invoice. This accelerates the next recon process.
        def process_invoice(i: ln.Invoice) -> Generator[Operation]:
            nonlocal unsettled_found

            # We have a unsettled invoice.
            if i.state != 1 and not unsettled_found:
                self._logger.debug(
                    f"Reconciliation found first unsettled invoice {i.add_index=}; "
                    f"{self._next_recon_index=}"
                )
                unsettled_found = True

            if not unsettled_found:
                self._next_recon_index = i.add_index

            yield from self._process_invoice(i, True)

        return StreamConverter(paginator, process_invoice)

    def _get_new_stream(self) -> Callable[[], Generator[Operation]]:
        dispatcher = self._lnd.subscribe_invoices_dispatcher
        return self._get_new_stream_from_dispatcher(dispatcher)

    def _process_invoice(
        self, i: ln.Invoice, recon_running: bool
    ) -> Generator[Operation]:
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
                self._store.get_invoice_id(i.add_index)
                return None
            except GetIdException:
                self._logger.debug(
                    f"Invoice reconciliation: {i.add_index=}; {i.settle_index=} not found."
                )
                pass

        invoice = self._new_invoice(i)

        yield new_operation_from_htlcs(
            txs=[invoice],
            htlcs=invoice.invoice_htlcs,
        )

    def _new_invoice(self, invoice: ln.Invoice) -> Invoice:
        """
        Creates an invoice object.
        """

        resolve_info = TransactionResolveInfo(
            resolve_time=sec_to_datetime(invoice.settle_date),
            resolve_type=TransactionResolveType.SETTLED,
        )
        # Create the invoice object
        return Invoice(
            uuid=Invoice.generate_uuid(self._store.ln_node_id, invoice.add_index),
            ln_node_id=self._store.ln_node_id,
            invoice_htlcs=[
                self._new_htlc(h, invoice.r_preimage) for h in invoice.htlcs
            ],
            r_hash=bytes_to_str(invoice.r_hash),
            payment_request=invoice.payment_request,
            creation_time=sec_to_datetime(invoice.creation_date),
            value_msat=invoice.value_msat,
            add_index=invoice.add_index,
            settle_index=invoice.settle_index,
            resolve_info=resolve_info,
        )

    def _new_htlc(self, htlc: ln.InvoiceHTLC, preimage: bytes) -> HtlcInvoice:

        resolve_info = HtlcResolveInfoSettled(
            resolve_time=sec_to_datetime(htlc.resolve_time),
            resolve_type=HtlcResolveType.SETTLED,
            preimage=bytes_to_str(preimage),
        )

        return HtlcInvoice(
            resolve_info=resolve_info,
            amt_msat=htlc.amt_msat,
            attempt_time=sec_to_datetime(htlc.accept_time),
            timelock=htlc.expiry_height,
            htlc_index=htlc.htlc_index,
            channel_id=str(htlc.chan_id),
            direction_type=HtlcDirectionType.INCOMING,
        )
