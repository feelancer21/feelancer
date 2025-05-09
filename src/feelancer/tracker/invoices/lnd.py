import datetime
from collections.abc import Callable, Generator

import pytz

from feelancer.grpc.client import StreamConverter
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.tracker.data import InvoiceNotFound, create_operation_from_htlcs
from feelancer.tracker.lnd import LndBaseTracker
from feelancer.tracker.models import (
    HtlcDirectionType,
    HtlcInvoice,
    HtlcResolveInfoSettled,
    HtlcResolveType,
    Invoice,
    Operation,
)
from feelancer.utils import bytes_to_str, sec_to_datetime

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds

type LndInvoiceReconSource = StreamConverter[Operation, ln.Invoice]


class LNDInvoiceTracker(LndBaseTracker):

    def _delete_orphaned_data(self) -> None:
        return None

    def _get_items_name(self) -> str:
        return "invoices"

    def _pre_sync_source(self) -> LndInvoiceReconSource:

        index_offset = self._store.get_max_invoice_add_index()
        self._logger.debug(f"Starting from index {index_offset} for {self._pub_key}")

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

        recon_start = datetime.datetime.now(tz=pytz.utc) - datetime.timedelta(
            seconds=RECON_TIME_INTERVAL
        )

        paginator = self._lnd.paginate_invoices(
            creation_date_start=int(recon_start.timestamp()),
        )

        return StreamConverter(
            paginator, lambda item: self._process_invoice(item, True)
        )

    def _get_new_stream(self) -> Callable[..., Generator[Operation]]:
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
                self._store.get_invoice_id(bytes_to_str(i.r_hash))
                return None
            except InvoiceNotFound:
                self._logger.debug(
                    f"Invoice reconciliation: {i.add_index=}; {i.settle_index=} not found."
                )
                pass

        invoice = self._create_invoice(i)

        yield create_operation_from_htlcs(
            txs=[invoice],
            htlcs=invoice.invoice_htlcs,
        )

    def _create_invoice(self, invoice: ln.Invoice) -> Invoice:
        """
        Creates an invoice object.
        """

        return Invoice(
            uuid=Invoice.generate_uuid(self._store.ln_node_id, invoice.add_index),
            ln_node_id=self._store.ln_node_id,
            invoice_htlcs=[
                self._create_htlc(h, invoice.r_preimage) for h in invoice.htlcs
            ],
            r_hash=bytes_to_str(invoice.r_hash),
            creation_time=sec_to_datetime(invoice.creation_date),
            value_msat=invoice.value_msat,
            add_index=invoice.add_index,
            settle_index=invoice.settle_index,
        )

    def _create_htlc(self, htlc: ln.InvoiceHTLC, preimage: bytes) -> HtlcInvoice:

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
