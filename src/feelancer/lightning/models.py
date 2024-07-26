from __future__ import annotations

from sqlalchemy import (
    BIGINT,
    SMALLINT,
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from feelancer.tasks.models import Base, DBRun


class DBLnNode(Base):
    __tablename__ = "ln_node"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)
    pub_key: Mapped[str] = mapped_column(String(66), nullable=False)
    runs: Mapped[list[DBLnRun]] = relationship("DBLnRun", back_populates="ln_node")
    channels: Mapped[list[DBLnChannelStatic]] = relationship(
        "DBLnChannelStatic", back_populates="ln_node"
    )


class DBLnRun(Base):
    __tablename__ = "ln_run"

    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), primary_key=True)
    run: Mapped[DBRun] = relationship(DBRun)

    ln_node_id: Mapped[int] = mapped_column(ForeignKey("ln_node.id"), nullable=False)
    ln_node: Mapped[DBLnNode] = relationship(DBLnNode, back_populates="runs")

    policies: Mapped[list[DBLnChannelPolicy]] = relationship(
        "DBLnChannelPolicy", back_populates="ln_run"
    )

    liquidity: Mapped[list[DBLnChannelLiquidity]] = relationship(
        "DBLnChannelLiquidity", back_populates="ln_run"
    )


class DBLnChannelPeer(Base):
    __tablename__ = "ln_channel_peer"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)
    pub_key: Mapped[str] = mapped_column(
        String(66), index=True, unique=True, nullable=False
    )

    channels: Mapped[list[DBLnChannelStatic]] = relationship(
        "DBLnChannelStatic",
        back_populates="peer",
    )


class DBLnChannelStatic(Base):
    """
    All channel data changing over time, like balances and fee_rates
    """

    __tablename__ = "ln_channel_static"
    __table_args__ = (UniqueConstraint("chan_id", "ln_node_id"),)

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)
    chan_id: Mapped[int] = mapped_column(BIGINT, nullable=False)
    chan_point: Mapped[str] = mapped_column(String, nullable=False)
    ln_node_id: Mapped[int] = mapped_column(ForeignKey("ln_node.id"), nullable=False)
    opening_height: Mapped[int] = mapped_column(Integer, nullable=False)
    peer_id: Mapped[int] = mapped_column(
        ForeignKey("ln_channel_peer.id"), nullable=False
    )
    capacity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    private: Mapped[bool] = mapped_column(Boolean, nullable=False)

    peer: Mapped[DBLnChannelPeer] = relationship(
        DBLnChannelPeer, back_populates="channels"
    )
    ln_node: Mapped[DBLnNode] = relationship(DBLnNode, back_populates="channels")

    liquidity: Mapped[list[DBLnChannelLiquidity]] = relationship(
        "DBLnChannelLiquidity",
        back_populates="static",
    )

    policies: Mapped[list[DBLnChannelPolicy]] = relationship(
        "DBLnChannelPolicy",
        back_populates="static",
    )


class DBLnChannelLiquidity(Base):
    """
    Liquidity in the channels.
    """

    __tablename__ = "ln_channel_liquidity"

    channel_static_id: Mapped[int] = mapped_column(
        ForeignKey("ln_channel_static.id"), index=True, primary_key=True
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ln_run.run_id"), index=True, primary_key=True, nullable=False
    )
    liquidity_out_settled_sat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    liquidity_out_pending_sat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    liquidity_in_settled_sat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    liquidity_in_pending_sat: Mapped[int] = mapped_column(BigInteger, nullable=False)

    static: Mapped[DBLnChannelStatic] = relationship(
        DBLnChannelStatic, back_populates="liquidity"
    )

    ln_run: Mapped[DBLnRun] = relationship(DBLnRun, back_populates="liquidity")


class DBLnChannelPolicy(Base):
    __tablename__ = "ln_channel_policy"
    run_id: Mapped[int] = mapped_column(ForeignKey("ln_run.run_id"), primary_key=True)
    channel_static_id: Mapped[int] = mapped_column(
        ForeignKey("ln_channel_static.id"), primary_key=True
    )
    sequence_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    local: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    fee_rate_ppm: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_fee_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_lock_delta: Mapped[int] = mapped_column(SMALLINT, nullable=False)
    min_htlc_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_htlc_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    inbound_fee_rate_ppm: Mapped[int] = mapped_column(BigInteger, nullable=False)
    inbound_base_fee_msat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_update: Mapped[int] = mapped_column(BigInteger, nullable=False)

    ln_run: Mapped[DBLnRun] = relationship(DBLnRun, back_populates="policies")

    static: Mapped[DBLnChannelStatic] = relationship(
        DBLnChannelStatic, back_populates="policies"
    )
