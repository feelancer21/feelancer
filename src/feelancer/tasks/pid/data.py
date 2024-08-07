from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Generator

from sqlalchemy.orm import Session, joinedload

from feelancer.lightning.data import DBLnRun, convert_from_channel_policy
from feelancer.lightning.enums import PolicyFetchType
from feelancer.lightning.models import DBLnChannelPolicy
from feelancer.tasks.result import TaskResult

from .aggregator import ChannelAggregator
from .config import PidConfig
from .controller import MarginController, PeerController, PidControllerParams
from .models import (
    Base,
    DBLnChannelPeer,
    DBLnChannelStatic,
    DBLnNode,
    DBPidController,
    DBPidMarginController,
    DBPidPeerController,
    DBPidResult,
    DBPidRun,
    DBRun,
)

if TYPE_CHECKING:
    from datetime import datetime

    from feelancer.lightning.chan_updates import PolicyRecommendation
    from feelancer.lightning.client import Channel, ChannelPolicy
    from feelancer.lightning.data import LightningSessionCache
    from feelancer.tasks.session import TaskSession

    from .ewma_pid import EwmaPID


def _get_last_pid_run(session: Session, pub_key: str) -> DBPidRun | None:
    res = (
        session.query(DBPidRun)
        .join(DBPidRun.ln_node)
        .filter(DBLnNode.pub_key == pub_key)
        .options(joinedload(DBPidRun.run))
        .order_by(DBPidRun.run_id.desc())
        .first()
    )
    return res


def _get_last_ln_run(session: Session, pid_run: DBPidRun | None) -> DBLnRun | None:
    if not pid_run:
        return None
    if not pid_run.run:
        return None

    res = session.query(DBLnRun).filter(DBLnRun.run == pid_run.run).first()
    return res


def _convert_to_pid_run(run: DBRun, ln_node: DBLnNode) -> DBPidRun:
    return DBPidRun(run=run, ln_node=ln_node)


def _convert_pid_result(
    policy_recommendation: PolicyRecommendation,
    channel_static: DBLnChannelStatic,
    pid_run: DBPidRun,
) -> DBPidResult:
    return DBPidResult(
        static=channel_static,
        pid_run=pid_run,
        feerate_recommended=policy_recommendation.feerate_ppm,
    )


def _convert_to_pid_controller(
    pid_params: PidControllerParams, ewma_pid: EwmaPID
) -> DBPidController:
    return DBPidController(
        alpha_d=pid_params.alpha_d,
        alpha_i=pid_params.alpha_i,
        c=pid_params.c,
        k_t=pid_params.k_t,
        k_p=pid_params.k_p,
        k_i=pid_params.k_i,
        k_d=pid_params.k_d,
        k_m=pid_params.k_m,
        shift=pid_params.shift,
        conversion_method=pid_params.conversion_method,
        delta_time=ewma_pid.delta_time,
        error=ewma_pid.error,
        error_ewma=ewma_pid.error_ewma,
        error_delta_residual=ewma_pid.error_delta_residual,
        gain=ewma_pid.gain,
        gain_p=ewma_pid.gain_p,
        gain_t=ewma_pid.gain_t,
        gain_i=ewma_pid.gain_i,
        gain_d=ewma_pid.gain_d,
        control_variable_last=ewma_pid.control_variable_last,
        control_variable=ewma_pid.control_variable,
        control_factor=ewma_pid.control_factor,
    )


def _convert_from_pid_controller(
    pid_controller: DBPidController,
) -> PidControllerParams:
    return PidControllerParams(
        conversion_method_str=pid_controller.conversion_method.name,
        shift=pid_controller.shift,
        alpha_i=pid_controller.alpha_i,
        alpha_d=pid_controller.alpha_d,
        c=pid_controller.c,
        k_t=pid_controller.k_t,
        k_p=pid_controller.k_p,
        k_i=pid_controller.k_i,
        k_d=pid_controller.k_d,
        k_m=pid_controller.k_m,
        error=pid_controller.error,
        error_ewma=pid_controller.error_ewma,
        error_delta_residual=pid_controller.error_delta_residual,
        control_factor=pid_controller.control_factor,
        delta_time=pid_controller.delta_time,
    )


def _convert_from_pid_peer_controller(
    pid_peer_controller: DBPidPeerController,
) -> PidControllerParams:
    return _convert_from_pid_controller(pid_peer_controller.pid_controller)


def _convert_to_pid_peer_controller(
    peer_controller: PeerController,
    channel_peer: DBLnChannelPeer,
    pid_run: DBPidRun,
) -> DBPidPeerController:
    return DBPidPeerController(
        pid_run=pid_run,
        peer=channel_peer,
        pid_controller=_convert_to_pid_controller(
            peer_controller.pid_controller_params, peer_controller.ewma_pid
        ),
        target=peer_controller.target,
    )


def _convert_from_margin_controller(
    margin_controller: DBPidMarginController,
) -> PidControllerParams:
    return _convert_from_pid_controller(margin_controller.pid_controller)


def _convert_to_margin_controller(
    pid_run: DBPidRun, margin_controller: MarginController
) -> DBPidMarginController:
    return DBPidMarginController(
        pid_run=pid_run,
        pid_controller=_convert_to_pid_controller(
            margin_controller.pid_controller_params, margin_controller.ewma_pid
        ),
        feerate_local=margin_controller.feerate_local,
        feerate_target=margin_controller.feerate_target,
    )


def _yield_peer_controller(
    ln_session: LightningSessionCache,
    pid_run: DBPidRun,
    peer_controller_map: dict[str, PeerController],
) -> Generator[DBPidPeerController | DBPidResult, None, None]:
    for pub_key, peer_controller in peer_controller_map.items():
        peer = ln_session.get_channel_peer(pub_key)

        yield _convert_to_pid_peer_controller(peer_controller, peer, pid_run)
        for res in _yield_pid_results(ln_session, peer_controller, pid_run):
            yield res


def _get_last_pid_peer_controller(
    session: Session, local_pub_key: str, peer_pub_key: str
) -> tuple[None, None] | tuple[datetime, PidControllerParams]:
    res = (
        session.query(DBPidPeerController)
        .join(DBPidPeerController.peer)
        .join(DBPidPeerController.pid_run)
        .join(DBPidRun.ln_node)
        .filter(
            DBLnNode.pub_key == local_pub_key, DBLnChannelPeer.pub_key == peer_pub_key
        )
        .options(
            joinedload(DBPidPeerController.pid_controller),
        )
        .order_by(DBPidRun.run_id.desc())
        .first()
    )

    if not res:
        return None, None

    return res.pid_run.run.timestamp_start, _convert_from_pid_controller(
        res.pid_controller
    )


def _get_historic_pid_peer_params(
    session: Session, local_pub_key: str, peer_pub_key: str
) -> list[tuple[datetime, PidControllerParams]]:
    res = [
        (p.pid_run.run.timestamp_start, _convert_from_pid_controller(p.pid_controller))
        for p in session.query(DBPidPeerController)
        .join(DBPidPeerController.peer)
        .join(DBPidPeerController.pid_run)
        .join(DBPidRun.ln_node)
        .filter(
            DBLnNode.pub_key == local_pub_key, DBLnChannelPeer.pub_key == peer_pub_key
        )
        .options(
            joinedload(DBPidPeerController.pid_controller),
        )
        .order_by(DBPidRun.run_id.asc())
        .all()
    ]
    return res


def _get_historic_pid_margin_params(
    session: Session, local_pub_key: str
) -> list[tuple[datetime, PidControllerParams]]:
    res = [
        (p.pid_run.run.timestamp_start, _convert_from_pid_controller(p.pid_controller))
        for p in session.query(DBPidMarginController)
        .join(DBPidMarginController.pid_run)
        .join(DBPidRun.ln_node)
        .filter(
            DBLnNode.pub_key == local_pub_key,
        )
        .options(
            joinedload(DBPidMarginController.pid_controller),
        )
        .order_by(DBPidRun.run_id.asc())
        .all()
    ]
    return res


def _get_last_policies_end(
    session: Session, last_ln_run: DBLnRun | None
) -> dict[str, dict[int, ChannelPolicy]]:
    if not last_ln_run:
        return {}

    policies_end = (
        session.query(DBLnChannelPolicy)
        .options(joinedload(DBLnChannelPolicy.static, DBLnChannelStatic.peer))
        .join(DBLnChannelPolicy.ln_run)
        .filter(
            DBLnChannelPolicy.ln_run == last_ln_run,
            DBLnChannelPolicy.fetch_type == PolicyFetchType.END,
            DBLnChannelPolicy.local,
        )
        .all()
    )

    return {
        p.static.peer.pub_key: {
            c.static.chan_id: convert_from_channel_policy(c)
            for c in policies_end
            if c.static.peer.pub_key == p.static.peer.pub_key
        }
        for p in policies_end
    }


def _get_last_margin_pid_params(
    session: Session, last_pid_run: DBPidRun | None
) -> PidControllerParams | None:
    if not last_pid_run:
        return None
    if res := (
        session.query(DBPidMarginController)
        .options(joinedload(DBPidMarginController.pid_controller))
        .join(DBPidMarginController.pid_run)
        .filter(DBPidMarginController.pid_run == last_pid_run)
        .first()
    ):
        margin_controller = _convert_from_margin_controller(res)
    else:
        margin_controller = None
    return margin_controller


def _get_last_peer_pid_params(
    session: Session, last_pid_run: DBPidRun | None
) -> dict[str, PidControllerParams]:
    if not last_pid_run:
        return {}

    return {
        p.peer.pub_key: _convert_from_pid_peer_controller(p)
        for p in session.query(DBPidPeerController)
        .options(
            joinedload(DBPidPeerController.pid_controller),
            joinedload(DBPidPeerController.peer),
        )
        .join(DBPidPeerController.pid_run)
        .filter(DBPidPeerController.pid_run == last_pid_run)
        .all()
    }


def _yield_pid_results(
    ln_session: LightningSessionCache,
    peer_controller: PeerController,
    pid_run: DBPidRun,
) -> Generator[DBPidResult, None, None]:
    for p in peer_controller.policy_recommendations():
        yield _convert_pid_result(p, ln_session.get_channel_static(p.channel), pid_run)


class Pid(TaskResult):
    def __init__(self, session: TaskSession) -> None:
        self.ln = session.ln
        self.db = session.db
        self.db.create_base(Base)

        self.timestamp_start = session.timestamp_start
        self.block_height = session.ln.lnclient.block_height

        self.config = PidConfig(session.get_task_config("pid"))

        last_pid_run = self._last_pid_run()
        if not last_pid_run:
            self.last_timestamp = None
            self.last_policies_end = {}
        else:
            self.last_timestamp = last_pid_run.run.timestamp_start
            last_ln_run = self._last_ln_run()
            self.last_policies_end = self._last_policies_end(last_ln_run)

        self.last_peer_pid_params = self._last_peer_pid_params(last_pid_run)
        self.last_margin_pid_params = self._last_margin_pid_params(last_pid_run)

        self.aggregator = ChannelAggregator.from_channels(
            config=self.config,
            policies_end_last=self.last_policies_end,
            block_height=self.block_height,
            channels=self.channels.values(),
        )

        self.margin_controller = MarginController.from_data(
            aggregator=self.aggregator,
            last_timestamp=self.last_timestamp,
            current_timestamp=self.timestamp_start,
            last_pid_params=self.last_margin_pid_params,
            current_pid_params=self.config.margin.pid_controller,
            historic_pid_params=self._historic_margin_pid_params,
        )

        self.peer_controller_map = self._peer_controller_map()
        session.add_result(self)

    def latest_pid_peer_params(
        self, peer_pub_key: str
    ) -> tuple[None, None] | tuple[datetime, PidControllerParams]:
        with self._db_session() as session:
            return _get_last_pid_peer_controller(
                session, self.ln.pubkey_local, peer_pub_key
            )

    def _init_pid_peer_params(
        self, pub_key
    ) -> tuple[datetime | None, PidControllerParams]:
        timestamp = None

        if not (pid_params := self.last_peer_pid_params.get(pub_key)):
            # TODO: Check the timestamp to avoid the assignment of
            # outdated pid_params
            timestamp, pid_params = self.latest_pid_peer_params(pub_key)

        if not timestamp:
            timestamp = self.last_timestamp

        if not pid_params:
            peer_config = self.config.peer_config(pub_key)
            pid_params = peer_config.pid_controller

        return timestamp, pid_params

    def _historic_margin_pid_params(
        self,
    ) -> list[tuple[datetime, PidControllerParams]]:
        with self._db_session() as session:
            return _get_historic_pid_margin_params(session, self.pubkey_local)

    def _historic_pid_peer_params(
        self, peer_pub_key: str
    ) -> Callable[..., list[tuple[datetime, PidControllerParams]]]:
        def fetch_history() -> list[tuple[datetime, PidControllerParams]]:
            with self._db_session() as session:
                return _get_historic_pid_peer_params(
                    session, self.pubkey_local, peer_pub_key
                )

        return fetch_history

    def _last_margin_pid_params(
        self, pid_run: DBPidRun | None
    ) -> PidControllerParams | None:
        with self._db_session() as session:
            return _get_last_margin_pid_params(session, pid_run)

    def _last_peer_pid_params(
        self, pid_run: DBPidRun | None
    ) -> dict[str, PidControllerParams]:
        with self._db_session() as session:
            return _get_last_peer_pid_params(session, pid_run)

    def _last_policies_end(
        self, ln_run: DBLnRun | None
    ) -> dict[str, dict[int, ChannelPolicy]]:
        with self._db_session() as session:
            return _get_last_policies_end(session, ln_run)

    def _last_pid_run(self) -> DBPidRun | None:
        with self._db_session() as session:
            return _get_last_pid_run(session, self.pubkey_local)

    def _last_ln_run(self) -> DBLnRun | None:
        with self._db_session() as session:
            return _get_last_ln_run(session, self._last_pid_run())

    def _peer_controller_map(self) -> dict[str, PeerController]:
        peer_controller_map: dict[str, PeerController] = {}

        for pub_key, channel_collection in self.aggregator.pid_collections():
            peer_config = self.config.peer_config(pub_key)
            init_timestamp, init_pid_params = self._init_pid_peer_params(pub_key)
            target = peer_config.target or self.aggregator.target_default

            peer_controller_map[pub_key] = PeerController.from_data(
                target=target,
                init_timestamp=init_timestamp,
                current_timestamp=self.timestamp_start,
                init_pid_params=init_pid_params,
                current_pid_params=peer_config.pid_controller,
                channel_collection=channel_collection,
                margin_controller=self.margin_controller,
                historic_pid_params=self._historic_pid_peer_params(pub_key),
            )

        return peer_controller_map

    @property
    def channels(self) -> dict[int, Channel]:
        return self.ln.channels

    @property
    def pubkey_local(self) -> str:
        return self.ln.pubkey_local

    def _db_session(self) -> Session:
        return self.db.session()

    def write_final_data(self, ln_session: LightningSessionCache) -> None:
        ln_session.set_channel_policies(PolicyFetchType.START, True)
        ln_session.set_channel_policies(PolicyFetchType.START, False)
        ln_session.set_channel_policies(PolicyFetchType.END, True)
        ln_session.channel_liquidity

        ln_session.db_session.add(ln_session.ln_run)
        ln_session.db_session.add_all(self._yield_results(ln_session))

    def _yield_results(
        self, ln_session: LightningSessionCache
    ) -> Generator[
        DBPidMarginController | DBPidPeerController | DBPidResult, None, None
    ]:
        pid_run = _convert_to_pid_run(ln_session.db_run, ln_session.ln_node)
        yield _convert_to_margin_controller(pid_run, self.margin_controller)
        for res in _yield_peer_controller(
            ln_session, pid_run, self.peer_controller_map
        ):
            yield res

    def policy_recommendations(self) -> Generator[PolicyRecommendation, None, None]:
        if self.config.db_only:
            return None

        for peer_controller in self.peer_controller_map.values():
            for p in peer_controller.policy_recommendations():
                yield p
