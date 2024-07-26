"""
Data model for the pid controller
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from feelancer.lightning.models import (
    Base,
    DBLnChannelPeer,
    DBLnChannelStatic,
    DBLnNode,
    DBRun,
)


class DBPidRun(Base):
    __tablename__ = "pid_run"

    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), primary_key=True)
    ln_node_id: Mapped[int] = mapped_column(ForeignKey("ln_node.id"), nullable=False)

    run: Mapped[DBRun] = relationship(DBRun)
    ln_node: Mapped[DBLnNode] = relationship(DBLnNode)


class DBPidEwmaController(Base):
    __tablename__ = "pid_ewma_controller"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)
    alpha_i: Mapped[float] = mapped_column(Float, nullable=False)
    alpha_d: Mapped[float] = mapped_column(Float, nullable=False)
    k_t: Mapped[float] = mapped_column(Float, nullable=False)
    k_p: Mapped[float] = mapped_column(Float, nullable=False)
    k_i: Mapped[float] = mapped_column(Float, nullable=False)
    k_d: Mapped[float] = mapped_column(Float, nullable=False)
    delta_time: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[float] = mapped_column(Float, nullable=False)
    error_ewma: Mapped[float] = mapped_column(Float, nullable=False)
    error_delta_residual: Mapped[float] = mapped_column(Float, nullable=False)
    gain_t: Mapped[float] = mapped_column(Float, nullable=False)
    gain_p: Mapped[float] = mapped_column(Float, nullable=False)
    gain_i: Mapped[float] = mapped_column(Float, nullable=False)
    gain_d: Mapped[float] = mapped_column(Float, nullable=False)
    gain: Mapped[float] = mapped_column(Float, nullable=False)
    shift: Mapped[float] = mapped_column(Float, nullable=False)
    control_variable: Mapped[float] = mapped_column(Float, nullable=False)


class DBPidMrController(Base):
    __tablename__ = "pid_mr_controller"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)
    alpha: Mapped[float] = mapped_column(Float, nullable=False)
    k_m: Mapped[float] = mapped_column(Float, nullable=False)
    delta_time: Mapped[float] = mapped_column(Float, nullable=False)
    gain: Mapped[float] = mapped_column(Float, nullable=False)
    control_variable: Mapped[float] = mapped_column(Float, nullable=False)


class DBPidSpreadController(Base):
    __tablename__ = "pid_spread_controller"

    ewma_controller_id: Mapped[int] = mapped_column(
        ForeignKey("pid_ewma_controller.id")
    )
    run_id: Mapped[int] = mapped_column(ForeignKey("pid_run.run_id"), primary_key=True)
    peer_id: Mapped[int] = mapped_column(
        ForeignKey("ln_channel_peer.id"), primary_key=True
    )

    target: Mapped[Float] = mapped_column(Float, nullable=False)

    ewma_controller: Mapped[DBPidEwmaController] = relationship(DBPidEwmaController)
    pid_run: Mapped[DBPidRun] = relationship(DBPidRun)
    peer: Mapped[DBLnChannelPeer] = relationship(DBLnChannelPeer)


class DBPidMarginController(Base):
    __tablename__ = "pid_margin_controller"

    mr_controller_id: Mapped[int] = mapped_column(
        ForeignKey("pid_mr_controller.id"), primary_key=True
    )
    run_id: Mapped[int] = mapped_column(ForeignKey("pid_run.run_id"), primary_key=True)

    mr_controller: Mapped[DBPidMrController] = relationship(DBPidMrController)
    pid_run: Mapped[DBPidRun] = relationship(DBPidRun)


class DBPidResult(Base):
    __tablename__ = "pid_result"

    channel_static_id: Mapped[int] = mapped_column(
        ForeignKey("ln_channel_static.id"), primary_key=True
    )
    run_id: Mapped[int] = mapped_column(ForeignKey("pid_run.run_id"), primary_key=True)
    margin_base: Mapped[float] = mapped_column(Float, nullable=False)
    margin_idiosyncratic: Mapped[float] = mapped_column(Float, nullable=False)
    spread: Mapped[float] = mapped_column(Float, nullable=False)

    pid_run: Mapped[DBPidRun] = relationship(DBPidRun)
    static: Mapped[DBLnChannelStatic] = relationship(DBLnChannelStatic)
