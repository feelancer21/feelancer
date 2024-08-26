"""
Database interactions for the pid controller.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import joinedload

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

    from sqlalchemy import Select

    from feelancer.data.db import FeelancerDB

    from .analytics import EwmaController, MrController
    from .controller import MarginController, PidResult, SpreadController

T = TypeVar("T")

DEFAULT_MAX_AGE_NEW_CHANNELS = 144
DEFAULT_MAX_AGE_SPREAD_HOURS = 0
DEFAULT_DB_ONLY = True
DEFAULT_SET_INBOUND = False


@dataclass
class EwmaControllerParams:
    """Parameters for an EwmaController"""

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


@dataclass
class MrControllerParams:
    """Parameters for a MrController"""

    k_m: float = 0
    alpha: float = 0
    control_variable: float = 0


def _convert_mr_controller(
    mr_controller: DBPidMrController,
) -> MrControllerParams:
    return MrControllerParams(
        k_m=mr_controller.k_m,
        alpha=mr_controller.alpha,
        control_variable=mr_controller.control_variable,
    )


def _convert_ewma_controller(
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


def _convert_spread_controller(
    pid_spread_controller: DBPidSpreadController,
) -> EwmaControllerParams:
    return _convert_ewma_controller(pid_spread_controller.ewma_controller)


def _convert_margin_controller(
    margin_controller: DBPidMarginController,
) -> MrControllerParams:
    return _convert_mr_controller(margin_controller.mr_controller)


def new_pid_run(run: DBRun, ln_node: DBLnNode) -> DBPidRun:
    return DBPidRun(run=run, ln_node=ln_node)


def new_pid_result(
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


def _new_ewma_controller(ewma_controller: EwmaController) -> DBPidEwmaController:
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


def _new_mr_controller(mr_controller: MrController) -> DBPidMrController:
    mr_params = mr_controller.mr_params
    return DBPidMrController(
        alpha=mr_params.alpha,
        k_m=mr_params.k_m,
        delta_time=mr_controller.delta_time,
        gain=mr_controller.gain,
        control_variable=mr_controller.control_variable,
    )


def new_spread_controller(
    spread_controller: SpreadController,
    channel_peer: DBLnChannelPeer,
    pid_run: DBPidRun,
) -> DBPidSpreadController:
    return DBPidSpreadController(
        pid_run=pid_run,
        peer=channel_peer,
        ewma_controller=_new_ewma_controller(spread_controller.ewma_controller),
        target=spread_controller.target,
    )


def new_margin_controller(
    pid_run: DBPidRun, margin_controller: MarginController
) -> DBPidMarginController:
    return DBPidMarginController(
        pid_run=pid_run,
        mr_controller=_new_mr_controller(margin_controller.mr_controller),
    )


def query_margin_controller(
    local_pub_key: str | None = None,
    run_id: int | None = None,
    order_by_run_id_asc: bool = False,
) -> Select[tuple[DBPidMarginController]]:
    """
    Returns a query of DBPidMarginController and its associated objects.
    """

    qry = (
        select(DBPidMarginController)
        .join(DBPidMarginController.pid_run)
        .join(DBPidRun.ln_node)
    )

    if local_pub_key:
        qry = qry.where(DBLnNode.pub_key == local_pub_key)

    if run_id:
        qry = qry.where(DBPidMarginController.run_id == run_id)

    qry = qry.options(
        joinedload(DBPidMarginController.mr_controller),
        joinedload(DBPidMarginController.pid_run).joinedload(DBPidRun.run),
    )

    if order_by_run_id_asc:
        qry = qry.order_by(DBPidRun.run_id.asc())

    return qry


def query_pid_run(
    pub_key: str | None = None, order_by_run_id_desc: bool = False
) -> Select[tuple[DBPidRun]]:
    qry = select(DBPidRun)

    qry = qry.join(DBPidRun.ln_node)

    if pub_key:
        qry = qry.where(DBLnNode.pub_key == pub_key)

    qry = qry.options(joinedload(DBPidRun.run))

    if order_by_run_id_desc:
        qry = qry.order_by(DBPidRun.run_id.desc())

    return qry


def query_spread_controller(
    local_pub_key: str | None = None,
    peer_pub_key: str | None = None,
    run_id: int | None = None,
    order_by_run_id_asc: bool = False,
    order_by_run_id_desc: bool = False,
) -> Select[tuple[DBPidSpreadController]]:
    """
    Returns a query of DBSpreadController and its associated objects.
    """

    qry = (
        select(DBPidSpreadController)
        .join(DBPidSpreadController.peer)
        .join(DBPidSpreadController.pid_run)
        .join(DBPidRun.ln_node)
    )
    if local_pub_key:
        qry = qry.where(DBLnNode.pub_key == local_pub_key)

    if peer_pub_key:
        qry = qry.where(DBLnChannelPeer.pub_key == peer_pub_key)

    if run_id:
        qry = qry.where(DBPidSpreadController.run_id == run_id)

    qry = qry.options(
        joinedload(DBPidSpreadController.ewma_controller),
        joinedload(DBPidSpreadController.peer),
        joinedload(DBPidSpreadController.pid_run).joinedload(DBPidRun.run),
    )

    if order_by_run_id_asc:
        qry = qry.order_by(DBPidRun.run_id.asc())

    if order_by_run_id_desc:
        qry = qry.order_by(DBPidRun.run_id.desc())

    return qry


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
    """
    The config for the pid model.
    """

    def __init__(
        self,
        config_dict: dict,
    ) -> None:
        """
        Validates the provided dictionary and stores values in variables.
        """

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

        # For clear config handling, the ewma controller parameters can be given
        # names. This assumes that named_ewma is a dictionary.
        named_ewma: dict | None = conf_copy.get("named_ewma")
        if named_ewma and not isinstance(named_ewma, dict):
            raise ValueError("'named_ewma' is not a valid dictionary!")

        # get_ewma_controller performs a lookup into named_ewma if a str is
        # provided as parameter.
        def get_ewma_controller(params: str | dict) -> EwmaControllerParams:
            ewma_params = None
            if isinstance(params, str) and named_ewma:
                ewma_params = named_ewma.get(params)

            if not ewma_params:
                if not isinstance(params, dict):
                    raise ValueError(
                        f"ewma_controller '{params}' is not valid. "
                        f"dict expected here."
                    )
                ewma_params = params

            return EwmaControllerParams(**ewma_params)

        if conf_copy.get("peers"):
            if conf_copy["peers"].get("defaults") and (
                peer_params := conf_copy["peers"]["defaults"].get("ewma_controller")
            ):
                conf_copy["peers"]["defaults"]["ewma_controller"] = get_ewma_controller(
                    peer_params
                )

            for peer in conf_copy["peers"].keys() - ["defaults"]:
                if peer_params := conf_copy["peers"][peer].get("ewma_controller"):
                    conf_copy["peers"][peer]["ewma_controller"] = get_ewma_controller(
                        peer_params
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
    """
    PidStore is the interface for all pid relevant data from the database.
    The methods return non ORM objects only.
    """

    def __init__(self, db: FeelancerDB, pubkey_local: str) -> None:
        self.db = db
        self.pubkey_local = pubkey_local
        self.db.create_base(Base)

    def ewma_params_last_by_peer(
        self, peer_pub_key: str
    ) -> tuple[None, None] | tuple[datetime, EwmaControllerParams]:
        """
        Returns the EwmaControllerParams of the last execution of a spread controller
        for a given peer and its execution time.
        """

        qry = query_spread_controller(
            local_pub_key=self.pubkey_local,
            peer_pub_key=peer_pub_key,
            order_by_run_id_desc=True,
        )

        def convert(c: DBPidSpreadController) -> tuple[datetime, EwmaControllerParams]:
            return c.pid_run.run.timestamp_start, _convert_ewma_controller(
                c.ewma_controller
            )

        return self.db.query_first(qry, convert, (None, None))

    def ewma_params_by_pub_key(
        self, peer_pub_key: str
    ) -> list[tuple[datetime, EwmaControllerParams, float]]:
        """
        Returns a tuple of the historic timestamp, the EwmaControllerParams and
        the delta time.
        """

        qry = query_spread_controller(
            local_pub_key=self.pubkey_local,
            peer_pub_key=peer_pub_key,
            order_by_run_id_asc=True,
        )

        def convert(
            c: DBPidSpreadController,
        ) -> tuple[datetime, EwmaControllerParams, float]:
            return (
                c.pid_run.run.timestamp_start,
                _convert_ewma_controller(c.ewma_controller),
                c.ewma_controller.delta_time,
            )

        return self.db.query_all_to_list(qry, convert)

    def ewma_params_by_run(self, run_id: int) -> dict[str, EwmaControllerParams]:
        """
        Returns all EwmaControllerParams for the given pid run which were used
        by the SpreadControllers.

        Key of the returned dict is the pubkey of the channel peer.
        """

        qry = query_spread_controller(run_id=run_id)

        def pub_key(c: DBPidSpreadController) -> str:
            return c.peer.pub_key

        return self.db.query_all_to_dict(qry, pub_key, _convert_spread_controller)

    def mr_params_by_run(self, run_id: int) -> MrControllerParams | None:
        """
        Returns the MrControllerParams of the MarginController for the given pid run.
        """

        qry = query_margin_controller(run_id=run_id, order_by_run_id_asc=True)

        return self.db.query_first(qry, _convert_margin_controller)

    def mr_params_history(
        self,
    ) -> list[tuple[datetime, MrControllerParams]]:
        """
        Returns the historic MrControllerParams of the MarginController with its
        timestamps.
        """

        qry = query_margin_controller(local_pub_key=self.pubkey_local)

        def convert(c: DBPidMarginController) -> tuple[datetime, MrControllerParams]:
            return (
                c.pid_run.run.timestamp_start,
                _convert_mr_controller(c.mr_controller),
            )

        return self.db.query_all_to_list(qry, convert)

    def pid_run_last(self) -> tuple[int, datetime] | tuple[None, None]:

        qry = query_pid_run(pub_key=self.pubkey_local, order_by_run_id_desc=True)

        def convert(r: DBPidRun) -> tuple[int, datetime]:
            return r.run_id, r.run.timestamp_start

        return self.db.query_first(qry, convert, (None, None))


class PidDictGen:
    """
    Provides methods for selecting data from the database. The results are
    generators of dictionaries.
    """

    def __init__(self, db: FeelancerDB) -> None:
        self.db = db

    def spread_controller(self) -> Generator[dict, None, None]:
        """
        Returns a Generator of for DBSpreadController and its joined data.
        """

        qry = query_spread_controller(order_by_run_id_asc=True)

        return self.db.qry_all_to_field_dict_gen(qry)

    def margin_controller(self) -> Generator[dict, None, None]:
        """
        Returns a Generator of for DBMarginController and its joined data.
        """

        qry = query_margin_controller(order_by_run_id_asc=True)

        return self.db.qry_all_to_field_dict_gen(qry)
