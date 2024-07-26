from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session, joinedload

from feelancer.lightning.data import DBLnRun, convert_from_channel_policy
from feelancer.lightning.models import DBLnChannelPolicy
from feelancer.utils import GenericConf, defaults_from_type, get_peers_config

from .models import (
    Base,
    DBLnChannelPeer,
    DBLnChannelStatic,
    DBLnNode,
    DBPidEwmaController,
    DBPidMarginController,
    DBPidMrController,
    DBPidResult,
    DBPidRun,
    DBPidSpreadController,
    DBRun,
)

if TYPE_CHECKING:
    from datetime import datetime

    from feelancer.data.db import FeelancerDB
    from feelancer.lightning.client import ChannelPolicy

    from .analytics import EwmaController, MrController
    from .controller import MarginController, PidResult, SpreadController

DEFAULT_MAX_AGE_NEW_CHANNELS = 144
DEFAULT_MAX_AGE_SPREAD_HOURS = 0
DEFAULT_DB_ONLY = True
DEFAULT_SET_INBOUND = False


@dataclass
class EwmaControllerParams:
    k_t: float = 0
    k_p: float = 0
    k_i: float = 0
    k_d: float = 0
    alpha_i: float = 0
    alpha_d: float = 0
    error: float = 0
    error_ewma: float = 0
    error_delta_residual: float = 0
    control_variable: float = 0


# Parameters for a mean reverting controller
@dataclass
class MrControllerParams:
    k_m: float = 0
    alpha: float = 0
    control_variable: float = 0


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


def convert_to_pid_run(run: DBRun, ln_node: DBLnNode) -> DBPidRun:
    return DBPidRun(run=run, ln_node=ln_node)


def convert_to_pid_result(
    pid_result: PidResult,
    channel_static: DBLnChannelStatic,
    pid_run: DBPidRun,
) -> DBPidResult:
    return DBPidResult(
        pid_run=pid_run,
        static=channel_static,
        margin_base=pid_result.margin_base,
        margin_idiosyncratic=pid_result.margin_idiosyncratic,
        spread=pid_result.spread,
    )


def _convert_to_ewma_controller(ewma_controller: EwmaController) -> DBPidEwmaController:
    ewma_params = ewma_controller.ewma_params
    return DBPidEwmaController(
        alpha_d=ewma_params.alpha_d,
        alpha_i=ewma_params.alpha_i,
        k_t=ewma_params.k_t,
        k_p=ewma_params.k_p,
        k_i=ewma_params.k_i,
        k_d=ewma_params.k_d,
        delta_time=ewma_controller.delta_time,
        error=ewma_params.error,
        error_ewma=ewma_params.error_ewma,
        error_delta_residual=ewma_params.error_delta_residual,
        gain=ewma_controller.gain,
        gain_p=ewma_controller.gain_p,
        gain_t=ewma_controller.gain_t,
        gain_i=ewma_controller.gain_i,
        gain_d=ewma_controller.gain_d,
        control_variable=ewma_params.control_variable,
        shift=ewma_controller.shift,
    )


def _convert_from_ewma_controller(
    ewma_controller: DBPidEwmaController,
) -> EwmaControllerParams:
    return EwmaControllerParams(
        alpha_i=ewma_controller.alpha_i,
        alpha_d=ewma_controller.alpha_d,
        k_t=ewma_controller.k_t,
        k_p=ewma_controller.k_p,
        k_i=ewma_controller.k_i,
        k_d=ewma_controller.k_d,
        error=ewma_controller.error,
        error_ewma=ewma_controller.error_ewma,
        error_delta_residual=ewma_controller.error_delta_residual,
        control_variable=ewma_controller.control_variable,
    )


def _convert_to_mr_controller(mr_controller: MrController) -> DBPidMrController:
    mr_params = mr_controller.mr_params
    return DBPidMrController(
        alpha=mr_params.alpha,
        k_m=mr_params.k_m,
        delta_time=mr_controller.delta_time,
        gain=mr_controller.gain,
        control_variable=mr_controller.control_variable,
    )


def _convert_from_mr_controller(
    mr_controller: DBPidMrController,
) -> MrControllerParams:
    return MrControllerParams(
        k_m=mr_controller.k_m,
        alpha=mr_controller.alpha,
        control_variable=mr_controller.control_variable,
    )


def _convert_from_spread_controller(
    pid_spread_controller: DBPidSpreadController,
) -> EwmaControllerParams:
    return _convert_from_ewma_controller(pid_spread_controller.ewma_controller)


def convert_to_spread_controller(
    spread_controller: SpreadController,
    channel_peer: DBLnChannelPeer,
    pid_run: DBPidRun,
) -> DBPidSpreadController:
    return DBPidSpreadController(
        pid_run=pid_run,
        peer=channel_peer,
        ewma_controller=_convert_to_ewma_controller(spread_controller.ewma_controller),
        target=spread_controller.target,
    )


def _convert_from_margin_controller(
    margin_controller: DBPidMarginController,
) -> MrControllerParams:
    return _convert_from_mr_controller(margin_controller.mr_controller)


def convert_to_margin_controller(
    pid_run: DBPidRun, margin_controller: MarginController
) -> DBPidMarginController:
    return DBPidMarginController(
        pid_run=pid_run,
        mr_controller=_convert_to_mr_controller(margin_controller.mr_controller),
    )


def _get_last_spread_controller(
    session: Session, local_pub_key: str, peer_pub_key: str
) -> tuple[None, None] | tuple[datetime, EwmaControllerParams]:
    """
    Returns the EwmaControllerParams of the last execution of spread controller
    for a given peer and its execution time.
    """
    res = (
        session.query(DBPidSpreadController)
        .join(DBPidSpreadController.peer)
        .join(DBPidSpreadController.pid_run)
        .join(DBPidRun.ln_node)
        .filter(
            DBLnNode.pub_key == local_pub_key, DBLnChannelPeer.pub_key == peer_pub_key
        )
        .options(
            joinedload(DBPidSpreadController.ewma_controller),
        )
        .order_by(DBPidRun.run_id.desc())
        .first()
    )

    if not res:
        return None, None

    return res.pid_run.run.timestamp_start, _convert_from_ewma_controller(
        res.ewma_controller
    )


def _get_historic_ewma_params(
    session: Session, local_pub_key: str, peer_pub_key: str
) -> list[tuple[datetime, EwmaControllerParams, float]]:
    res = [
        (
            p.pid_run.run.timestamp_start,
            _convert_from_ewma_controller(p.ewma_controller),
            p.ewma_controller.delta_time,
        )
        for p in session.query(DBPidSpreadController)
        .join(DBPidSpreadController.peer)
        .join(DBPidSpreadController.pid_run)
        .join(DBPidRun.ln_node)
        .filter(
            DBLnNode.pub_key == local_pub_key, DBLnChannelPeer.pub_key == peer_pub_key
        )
        .options(
            joinedload(DBPidSpreadController.ewma_controller),
        )
        .order_by(DBPidRun.run_id.asc())
        .all()
    ]
    return res


def _get_historic_mr_params(
    session: Session, local_pub_key: str
) -> list[tuple[datetime, MrControllerParams]]:
    res = [
        (
            p.pid_run.run.timestamp_start,
            _convert_from_mr_controller(p.mr_controller),
        )
        for p in session.query(DBPidMarginController)
        .join(DBPidMarginController.pid_run)
        .join(DBPidRun.ln_node)
        .filter(
            DBLnNode.pub_key == local_pub_key,
        )
        .options(
            joinedload(DBPidMarginController.mr_controller),
        )
        .order_by(DBPidRun.run_id.asc())
        .all()
    ]
    return res


def _get_last_policies(
    session: Session, last_ln_run: DBLnRun | None, sequence_id: int
) -> dict[int, ChannelPolicy]:
    if not last_ln_run:
        return {}

    policies_end = (
        session.query(DBLnChannelPolicy)
        .options(joinedload(DBLnChannelPolicy.static, DBLnChannelStatic.peer))
        .join(DBLnChannelPolicy.ln_run)
        .filter(
            DBLnChannelPolicy.ln_run == last_ln_run,
            DBLnChannelPolicy.sequence_id == sequence_id,
            DBLnChannelPolicy.local,
        )
        .all()
    )

    return {c.static.chan_id: convert_from_channel_policy(c) for c in policies_end}


def _get_last_mr_params(
    session: Session, pid_run: DBPidRun | None
) -> MrControllerParams | None:
    """
    Returns the MrControllerParams of the MarginController for the given pid run.
    """
    if not pid_run:
        return None
    if res := (
        session.query(DBPidMarginController)
        .options(joinedload(DBPidMarginController.mr_controller))
        .join(DBPidMarginController.pid_run)
        .filter(DBPidMarginController.pid_run == pid_run)
        .first()
    ):
        margin_controller = _convert_from_margin_controller(res)
    else:
        margin_controller = None
    return margin_controller


def _get_last_ewma_params(
    session: Session, pid_run: DBPidRun | None
) -> dict[str, EwmaControllerParams]:
    """
    Returns all EwmaControllerParams for the given pid run which were used
    by the SpreadControllers.

    Key of the returned dict is the pubkey of the channel peer.
    """
    if not pid_run:
        return {}

    return {
        p.peer.pub_key: _convert_from_spread_controller(p)
        for p in session.query(DBPidSpreadController)
        .options(
            joinedload(DBPidSpreadController.ewma_controller),
            joinedload(DBPidSpreadController.peer),
        )
        .join(DBPidSpreadController.pid_run)
        .filter(DBPidSpreadController.pid_run == pid_run)
        .all()
    }


@dataclass
class PidSpreadControllerConfig(GenericConf):
    lambda_epsilon: float = 1e-4
    ewma_controller: EwmaControllerParams = field(
        default_factory=lambda: EwmaControllerParams()
    )
    target: int | None = None
    fee_rate_new_local: int = 21000
    fee_rate_new_remote: int = 0
    margin_idiosyncratic: float = 0


@dataclass
class PidMarginControllerConfig(GenericConf):
    mr_controller: MrControllerParams = field(
        default_factory=lambda: MrControllerParams()
    )


class PidConfig:
    def __init__(
        self,
        config_dict: dict,
    ) -> None:
        conf_copy = deepcopy(config_dict)

        if not (exclude_pubkeys := conf_copy.get("exclude_pubkeys")):
            self.exclude_pubkeys = []
        elif not isinstance(exclude_pubkeys, list):
            raise TypeError("'pid.exclude_pubkeys' not a list")
        else:
            self.exclude_pubkeys = exclude_pubkeys

        if not (exclude_chanids := conf_copy.get("exclude_chanids")):
            self.exclude_chanids = []
        elif not isinstance(exclude_chanids, list):
            raise TypeError("'pid.exclude_chanids' not a list")
        else:
            self.exclude_chanids = exclude_chanids

        if conf_copy.get("margin"):
            if fl_params := conf_copy["margin"].get("mr_controller"):
                conf_copy["margin"]["mr_controller"] = MrControllerParams(**fl_params)

        self.max_age_new_channels = int(
            conf_copy.get("max_age_new_channels") or DEFAULT_MAX_AGE_NEW_CHANNELS
        )

        self.max_age_spread_hours = int(
            conf_copy.get("max_age_spread_hours") or DEFAULT_MAX_AGE_SPREAD_HOURS
        )

        if isinstance(db_only := conf_copy.get("db_only"), bool):
            self.db_only = db_only
        elif db_only is None:
            self.db_only = DEFAULT_DB_ONLY
        else:
            raise TypeError("'db_only' is not a bool")

        if isinstance(set_inbound := conf_copy.get("set_inbound"), bool):
            self.set_inbound = set_inbound
        elif set_inbound is None:
            self.set_inbound = DEFAULT_SET_INBOUND
        else:
            raise TypeError("'set_inbound' is not a bool")

        self.margin = defaults_from_type(
            PidMarginControllerConfig, conf_copy.get("margin")
        )

        if conf_copy.get("peers"):
            if conf_copy["peers"].get("defaults") and (
                peer_params := conf_copy["peers"]["defaults"].get("ewma_controller")
            ):
                conf_copy["peers"]["defaults"]["ewma_controller"] = (
                    EwmaControllerParams(**peer_params)
                )

            for peer in conf_copy["peers"].keys() - ["defaults"]:
                if peer_params := conf_copy["peers"][peer].get("ewma_controller"):
                    conf_copy["peers"][peer]["ewma_controller"] = EwmaControllerParams(
                        **peer_params
                    )

        self.peers = get_peers_config(PidSpreadControllerConfig, conf_copy["peers"])

        self.pin_peer: str | None = None
        try:
            if pin_conf := conf_copy.get("pin"):
                self.pin_peer = str(pin_conf["peer"])
                self.pin_method = str(pin_conf["method"])
                if self.pin_method not in ["fee_rate", "spread"]:
                    raise ValueError("pid.pin.method is not 'fee_rate' or 'spread'")
                self.pin_value = float(pin_conf["value"])

        except Exception as e:
            raise ValueError(f"Cannot parse section 'pid.pin': {e}")

    def peer_config(self, pub_key: str) -> PidSpreadControllerConfig:
        if not (peer_config := self.peers.get(pub_key)):
            peer_config = self.peers["default"]
        return peer_config


class PidStore:
    def __init__(self, db: FeelancerDB, pubkey_local: str) -> None:
        self.db = db
        self.pubkey_local = pubkey_local
        self.db.create_base(Base)

    def _db_session(self) -> Session:
        return self.db.session()

    def historic_mr_params(
        self,
    ) -> list[tuple[datetime, MrControllerParams]]:
        """
        Returns the MrControllerParams of the MarginController for the given pid run.
        """

        with self._db_session() as session:
            return _get_historic_mr_params(session, self.pubkey_local)

    def historic_ewma_params(
        self, peer_pub_key: str
    ) -> list[tuple[datetime, EwmaControllerParams, float]]:
        with self._db_session() as session:
            return _get_historic_ewma_params(session, self.pubkey_local, peer_pub_key)

    def last_mr_params(self, pid_run: DBPidRun | None) -> MrControllerParams | None:
        with self._db_session() as session:
            return _get_last_mr_params(session, pid_run)

    def last_ewma_params(
        self, pid_run: DBPidRun | None
    ) -> dict[str, EwmaControllerParams]:
        """
        Returns all EwmaControllerParams for the given pid run which were used
        by the SpreadControllers.

        Key of the returned dict is the pubkey of the channel peer.
        """

        with self._db_session() as session:
            return _get_last_ewma_params(session, pid_run)

    def last_policies_end(self, ln_run: DBLnRun | None) -> dict[int, ChannelPolicy]:
        with self._db_session() as session:
            return _get_last_policies(session, ln_run, 1)

    def last_pid_run(self) -> DBPidRun | None:
        with self._db_session() as session:
            return _get_last_pid_run(session, self.pubkey_local)

    def last_ln_run(self) -> DBLnRun | None:
        with self._db_session() as session:
            return _get_last_ln_run(session, self.last_pid_run())

    def last_spread_controller_params(
        self, peer_pub_key: str
    ) -> tuple[None, None] | tuple[datetime, EwmaControllerParams]:
        """
        Returns the EwmaControllerParams of the last execution of spread controller
        for a given peer and its execution time.
        """

        with self._db_session() as session:
            return _get_last_spread_controller(session, self.pubkey_local, peer_pub_key)
