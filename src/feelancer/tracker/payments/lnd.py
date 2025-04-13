import datetime
from collections.abc import Callable, Generator

import pytz

from feelancer.grpc.client import StreamConverter
from feelancer.lnd.grpc_generated import lightning_pb2 as ln
from feelancer.tracker.data import (
    GraphNodeNotFound,
    GraphPathNotFound,
    PaymentNotFound,
    PaymentRequestNotFound,
)
from feelancer.tracker.lnd import LndBaseTracker
from feelancer.tracker.models import (
    Failure,
    FailureCode,
    GraphPath,
    Hop,
    HTLCAttempt,
    HTLCStatus,
    Payment,
    PaymentFailureReason,
    PaymentHtlcResolveInfo,
    PaymentRequest,
    PaymentResolveInfo,
    PaymentStatus,
    Route,
)
from feelancer.utils import ns_to_datetime, sha256_supports_str

RECON_TIME_INTERVAL = 30 * 24 * 3600  # 30 days in seconds

type LndPaymentReconSource = StreamConverter[HTLCAttempt, ln.Payment]


class LNDPaymentTracker(LndBaseTracker):

    def _delete_orphaned_data(self) -> None:
        self._store.delete_orphaned_payments()

    def _get_items_name(self) -> str:
        return "payments"

    def _pre_sync_source(self) -> LndPaymentReconSource:

        index_offset = self._store.get_max_payment_index()
        self._logger.debug(f"Starting from index {index_offset} for {self._pub_key}")

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
    ) -> Generator[HTLCAttempt]:

        return self._process_payment(item, recon_running)

    def _new_recon_source(self) -> LndPaymentReconSource:

        recon_start = datetime.datetime.now(tz=pytz.utc) - datetime.timedelta(
            seconds=RECON_TIME_INTERVAL
        )
        paginator = self._lnd.paginate_payments(
            include_incomplete=True,
            creation_date_start=int(recon_start.timestamp()),
        )

        return StreamConverter(
            paginator, lambda item: self._process_payment(item, True)
        )

    def _get_new_stream(self) -> Callable[..., Generator[HTLCAttempt]]:
        dispatcher = self._lnd.track_payments_dispatcher
        return self._get_new_stream_from_dispatcher(dispatcher)

    def _process_payment(
        self, p: ln.Payment, recon_running: bool
    ) -> Generator[HTLCAttempt]:
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
            except PaymentNotFound:
                self._logger.debug(
                    f"Payment reconciliation: {p.payment_index=} not found."
                )
                pass

        payment = self._create_payment(p)

        # Check if we have already stored the payment request in the database.
        # Maybe from the last run.
        try:
            payment.payment_request_id = self._store.get_payment_request_id(
                p.payment_hash
            )

        except PaymentRequestNotFound:
            payment.payment_request = PaymentRequest(
                payment_hash=p.payment_hash,
                payment_request=p.payment_request,
            )

        for h in p.htlcs:
            yield self._create_htlc_attempt(h, payment)

    def _create_payment(self, payment: ln.Payment) -> Payment:
        """
        Converts a payment object from the LND gRPC API to a Payment
        """

        # We are storing resolved payments at the moment. Hence we can create
        # the resolve info object directly. Maybe a TODO for the future if we
        # want to store unresolved payments too.
        resolve_info = PaymentResolveInfo(
            status=PaymentStatus(payment.status),
            failure_reason=PaymentFailureReason(payment.failure_reason),
            value_msat=payment.value_msat,
            fee_msat=payment.fee_msat,
        )

        return Payment(
            ln_node_id=self._store.ln_node_id,
            creation_time=ns_to_datetime(payment.creation_time_ns),
            payment_index=payment.payment_index,
            resolve_info=resolve_info,
        )

    def _create_htlc_attempt(
        self, attempt: ln.HTLCAttempt, payment: Payment
    ) -> HTLCAttempt:

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

        resolve_info = self._create_htlc_resolve_info(
            attempt, route.hops, last_used_hop_index, path
        )

        htlc_attempt = HTLCAttempt(
            payment=payment,
            attempt_id=attempt.attempt_id,
            attempt_time=ns_to_datetime(attempt.attempt_time_ns),
            route=route,
            resolve_info=resolve_info,
        )

        return htlc_attempt

    def _create_route(self, route: ln.Route) -> tuple[Route, list[int]]:

        hops: list[Hop] = []
        path: list[int] = []

        # For data analysis we want to store the first hop as a separate entry.
        node_id = self._get_graph_node_id(self._pub_key)
        hop_orm = Hop(
            position_id=0,
            expiry=route.total_time_lock,
            amt_to_forward_msat=route.total_amt_msat,
            fee_msat=0,
            node_id=node_id,
            node_outgoing_id=None,
            node_incoming_id=None,
        )
        hops.append(hop_orm)

        for i, hop in enumerate(route.hops):
            last_node_id = node_id
            node_id = self._get_graph_node_id(hop.pub_key)
            path.append(node_id)

            hop_orm.node_outgoing_id = node_id
            hop_orm = Hop(
                position_id=i + 1,
                expiry=hop.expiry,
                amt_to_forward_msat=hop.amt_to_forward_msat,
                fee_msat=hop.fee_msat,
                node_id=node_id,
                node_outgoing_id=None,
                node_incoming_id=last_node_id,
            )
            hops.append(hop_orm)

        path_id = self._get_graph_path_id(path)

        res_route = Route(
            total_time_lock=route.total_time_lock,
            total_amt_msat=route.total_amt_msat,
            total_fees_msat=route.total_fees_msat,
            hops=hops,
            path_id=path_id,
        )

        return res_route, path

    def _create_htlc_resolve_info(
        self,
        attempt: ln.HTLCAttempt,
        hops: list[Hop],
        last_used_hop_index: int | None,
        path: list[int],
    ) -> PaymentHtlcResolveInfo | None:
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
            failure = self._create_failure(attempt.failure, source_hop)
        else:
            failure = None

        if last_used_hop_index is not None and last_used_hop_index >= 0:
            path_success = path[:last_used_hop_index]
            path_success_id = self._get_graph_path_id(path_success)
        else:
            path_success_id = None

        return PaymentHtlcResolveInfo(
            resolve_time=resolve_time,
            status=HTLCStatus(attempt.status),
            failure=failure,
            path_success_id=path_success_id,
            num_hops_successful=last_used_hop_index,
        )

    def _create_failure(self, failure: ln.Failure, source_hop: Hop | None) -> Failure:
        return Failure(
            code=FailureCode(failure.code),
            source_index=failure.failure_source_index,
            source_hop=source_hop,
        )

    def _get_graph_node_id(self, pub_key: str) -> int:
        """
        Returns the id of a graph node. If not found it will be added to the database.
        """

        try:
            return self._store.get_graph_node_id(pub_key)
        except GraphNodeNotFound:
            return self._store.add_graph_node(pub_key)

    def _get_graph_path_id(self, path: list[int]) -> int:
        sha_path = sha256_supports_str(path)
        try:
            path_id = self._store.get_graph_path_id(sha_path)
        except GraphPathNotFound:
            path_id = self._store.add_graph_path(
                GraphPath(sha256_sum=sha_path, node_ids=path)
            )
        return path_id
