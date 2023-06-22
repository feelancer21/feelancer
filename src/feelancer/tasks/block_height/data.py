"""
Demo for an additional task with it's own simple data structure
"""
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from feelancer.lightning.data import LightningSessionCache
from feelancer.tasks.models import Base, DBRun
from feelancer.tasks.result import TaskResult
from feelancer.tasks.session import TaskSession


class DBBlockHeight(Base):
    __tablename__ = "block_height"

    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), primary_key=True)
    block_height: Mapped[int] = mapped_column(Integer, nullable=False)

    run: Mapped[DBRun] = relationship(DBRun)


class BlochHeightWriter(TaskResult):
    def __init__(self, session: TaskSession):
        session.db.create_base(Base)
        self.block_height = session.ln.lnclient.block_height
        session.add_result(self)

    def write_final_data(self, ln_session: LightningSessionCache) -> None:
        db_run = ln_session.db_run
        ln_session.db_session.add(
            DBBlockHeight(run=db_run, block_height=self.block_height)
        )
        ln_session.channel_liquidity
        ln_session.db_session.add(ln_session.ln_node)

    def policy_recommendations(self):
        yield None
