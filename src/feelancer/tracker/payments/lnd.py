import datetime
from collections.abc import Callable, Generator

import pytz

from feelancer.data.db import GetIdException
from feelancer.grpc.client import StreamConverter
from feelancer.lightning.lnd import LNDClient
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.tracker.data import TrackerStore, create_operation_from_htlcs
from feelancer.tracker.lnd import LndBaseTracker
from feelancer.tracker.models import (
    FailureCode,
    Hop,
    HtlcDirectionType,
    HtlcPayment,
    HtlcResolveInfo,
    HtlcResolveInfoPayment,
    HtlcResolveInfoPaymentFailed,
    HtlcResolveInfoSettled,
    Operation,
    Payment,
    PaymentFailureReason,
    PaymentRequest,
    PaymentResolveInfo,
    Route,
    TransactionResolveInfo,
    TransactionResolveType,
)
from feelancer.utils import ns_to_datetime

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds

type LndPaymentReconSource = StreamConverter[Operation, ln.Payment]


class LNDPaymentTracker(LndBaseTracker):
    def __init__(self, lnd: LNDClient, store: TrackerStore):
        super().__init__(lnd, store)

        # payment index for start of the next recon. This is set during a recon
        # and is the  payment_index of the first unsettled payment. If all
        # payments are settled we use last payment_index.
        self._next_recon_index: int = 0

    def _delete_orphaned_data(self) -> None:
        self._store.delete_orphaned_payments()

    def _get_items_name(self) -> str:
        return "payments"

    def _pre_sync_source(self) -> LndPaymentReconSource:

        index_offset = self._store.get_max_payment_index()
        self._logger.debug(
            f"Starting pre sync from index {index_offset} for {self._pub_key}"
        )

        paginator = self._lnd.paginate_payments(
            index_offset=index_offset, include_incomplete=True
        )

        return StreamConverter(
            paginator, lambda item: self._process_payment(item, False)
        )

    def _process_item_stream(
        self,
        item: ln.Payment,
        recon_running: bool,
    ) -> Generator[Operation]:

        return self._process_payment(item, recon_running)

    def _new_recon_source(self) -> LndPaymentReconSource:

        self._logger.debug(
            f"Starting recon from index {self._next_recon_index} for {self._pub_key}"
        )

        recon_start = datetime.datetime.now(tz=pytz.utc) - datetime.timedelta(
            seconds=RECON_TIME_INTERVAL
        )
        paginator = self._lnd.paginate_payments(
            include_incomplete=True,
            index_offset=self._next_recon_index,
            creation_date_start=int(recon_start.timestamp()),
        )

        unsettled_found: bool = False

        # Closure to update the next_recon_index until we found
        # a unsettled payment. This accelerates the next recon process.
        def process_payment(p: ln.Payment) -> Generator[Operation]:
            nonlocal unsettled_found

            # We have a unsettled payment.
            if p.status not in [2, 3] and not unsettled_found:
                self._logger.debug(
                    f"Reconciliation found first unsettled payment {p.payment_index=}; "
                    f"{self._next_recon_index=}"
                )
                unsettled_found = True

            if not unsettled_found:
                self._next_recon_index = p.payment_index

            yield from self._process_payment(p, True)

        return StreamConverter(paginator, process_payment)

    def _get_new_stream(self) -> Callable[..., Generator[Operation]]:
        dispatcher = self._lnd.track_payments_dispatcher
        return self._get_new_stream_from_dispatcher(dispatcher)

    def _process_payment(
        self, p: ln.Payment, recon_running: bool
    ) -> Generator[Operation]:
        """
        Callback function for the subscription. Converts the payment object
        to an Iterable of HTLCAttempt objects.
        """

        # only process status SUCCEEDED or FAILED
        if p.status not in [2, 3]:
            return

        # If we are in the reconciliation process we check if the payment is already
        # stored in the database. If so we skip it.
        # Outside of the reconciliation process this check is not necessary, because
        # we can assume that the payment is not stored in the database.
        if recon_running:
            try:
                self._store.get_payment_id(p.payment_index)
                return
            except GetIdException:
                self._logger.debug(
                    f"Payment reconciliation: {p.payment_index=} not found."
                )
                pass

        # There are payment messages with no htlcs, which we are not interested in.
        # We skip these messages to avoid handling of unnecessary exceptions.
        if len(p.htlcs) == 0:
            return

        payment = self._create_payment(p)

        # Check if we have already stored the payment request in the database.
        # Maybe from the last run.
        try:
            payment.payment_request_id = self._store.get_payment_request_id(
                p.payment_hash
            )

        except GetIdException:
            payment.payment_request = PaymentRequest(
                payment_hash=p.payment_hash,
                payment_request=p.payment_request if p.payment_request != "" else None,
            )

        yield create_operation_from_htlcs(txs=[payment], htlcs=payment.htlcs)

    def _create_payment(self, payment: ln.Payment) -> Payment:
        """
        Converts a payment object from the LND gRPC API to a Payment
        """

        # We are storing resolved payments at the moment. Hence we can create
        # the resolve info object directly. Maybe a TODO for the future if we
        # want to store unresolved payments too.
        payment_resolve_info = PaymentResolveInfo(
            failure_reason=PaymentFailureReason(payment.failure_reason),
            value_msat=payment.value_msat,
            fee_msat=payment.fee_msat,
        )

        resolve_info = TransactionResolveInfo(
            resolve_type=TransactionResolveType(payment.status),
            resolve_time=ns_to_datetime(
                max([h.resolve_time_ns for h in payment.htlcs])
            ),
        )

        return Payment(
            uuid=Payment.generate_uuid(self._store.ln_node_id, payment.payment_index),
            ln_node_id=self._store.ln_node_id,
            creation_time=ns_to_datetime(payment.creation_time_ns),
            payment_index=payment.payment_index,
            payment_resolve_info=payment_resolve_info,
            resolve_info=resolve_info,
            htlcs=[self._create_htlc(h) for h in payment.htlcs],
        )

    def _create_htlc(self, attempt: ln.HTLCAttempt) -> HtlcPayment:

        # Determination of the index of the last used hop. It is the failure source
        # index if the attempt failed. If the attempt succeeded it is the receiver
        # of the attempt.
        if attempt.status == 2 and attempt.HasField("failure"):
            last_used_hop_index = attempt.failure.failure_source_index
        elif attempt.status == 1:
            last_used_hop_index = len(attempt.route.hops)
        else:
            last_used_hop_index = None

        route, path = self._create_route(attempt.route)

        resolve_info = self._create_htlc_resolve_info(attempt, route.hops)

        resolve_payment_info = self._create_htlc_resolve_payment_info(
            last_used_hop_index, path
        )

        if len(attempt.route.hops) > 0:
            chan_out = str(attempt.route.hops[0].chan_id)
        else:
            chan_out = None

        htlc = HtlcPayment(
            amt_msat=attempt.route.total_amt_msat,
            attempt_time=ns_to_datetime(attempt.attempt_time_ns),
            channel_id=chan_out,
            direction_type=HtlcDirectionType.OUTGOING,
            timelock=attempt.route.total_time_lock,
            attempt_id=attempt.attempt_id,
            route=route,
            resolve_info=resolve_info,
            resolve_payment_info=resolve_payment_info,
        )

        return htlc

    def _create_route(self, route: ln.Route) -> tuple[Route, list[int]]:

        hops: list[Hop] = []
        path: list[int] = []

        # For data analysis we want to store the first hop as a separate entry.
        node_id = self._store.get_graph_node_id(self._pub_key)
        hop_orm = Hop(
            position_id=0,
            expiry=route.total_time_lock,
            amt_to_forward_msat=route.total_amt_msat,
            fee_msat=0,
            node_id=node_id,
            outgoing_node_id=None,
            incoming_node_id=None,
        )
        hops.append(hop_orm)

        for i, hop in enumerate(route.hops):
            last_node_id = node_id
            node_id = self._store.get_graph_node_id(hop.pub_key)
            path.append(node_id)

            hop_orm.outgoing_node_id = node_id
            hop_orm = Hop(
                position_id=i + 1,
                expiry=hop.expiry,
                amt_to_forward_msat=hop.amt_to_forward_msat,
                fee_msat=hop.fee_msat,
                node_id=node_id,
                outgoing_node_id=None,
                incoming_node_id=last_node_id,
            )
            hops.append(hop_orm)

        path_id = self._get_graph_path_id(path)

        res_route = Route(
            total_fees_msat=route.total_fees_msat,
            hops=hops,
            path_id=path_id,
        )

        return res_route, path

    def _create_htlc_resolve_info(
        self, attempt: ln.HTLCAttempt, hops: list[Hop]
    ) -> HtlcResolveInfo | None:
        """
        Creates the resolve info object for the HTLCAttempt. This object is used
        to store the resolve information in the database.
        """

        # If htlc attempt is in flight the htlc is not resolved and we return None.
        if attempt.status == 0:
            return None

        if attempt.resolve_time_ns > 0:
            resolve_time = ns_to_datetime(attempt.resolve_time_ns)
        else:
            resolve_time = None

        # If the attempt failed we store the failure information. For succeeded
        # attempts we don't need to store this information.
        if attempt.status == 2 and attempt.HasField("failure"):
            try:
                source_hop = hops[attempt.failure.failure_source_index]
            except IndexError:
                source_hop = None
                self._logger.warning(
                    f"Failure source index out of bounds: {attempt.failure.failure_source_index=}, ",
                    f"{attempt.attempt_id=}",
                )

            return HtlcResolveInfoPaymentFailed(
                resolve_time=resolve_time,
                code=FailureCode(attempt.failure.code),
                source_index=attempt.failure.failure_source_index,
                source_hop=source_hop,
            )

        else:
            return HtlcResolveInfoSettled(
                resolve_time=resolve_time,
                preimage=None,
            )

    def _create_htlc_resolve_payment_info(
        self,
        last_used_hop_index: int | None,
        path: list[int],
    ) -> HtlcResolveInfoPayment:

        if last_used_hop_index is not None and last_used_hop_index >= 0:
            path_success = path[:last_used_hop_index]
            path_success_id = self._get_graph_path_id(path_success)
        else:
            path_success_id = None

        return HtlcResolveInfoPayment(
            path_success_id=path_success_id,
            num_hops_successful=last_used_hop_index,
        )

    def _get_graph_path_id(self, path: list[int]) -> int:

        return self._store.get_graph_path_id(tuple(path))
