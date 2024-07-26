from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DBRun(Base):
    __tablename__ = "run"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)
    timestamp_start: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), index=True, nullable=False
    )
    timestamp_end: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), index=True, nullable=False
    )
