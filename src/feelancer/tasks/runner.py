from __future__ import annotations

from lndgrpc import LNDClient as LndGrpcClient

from feelancer.config import FeelancerConfig
from feelancer.data.db import FeelancerDB
from feelancer.lightning.data import LightningCache
from feelancer.lightning.lnd.client import LNDClient
from feelancer.tasks.writer import RunWriter

from .session import TaskSession


class TaskRunner:
    def __init__(self, config_dict: dict):
        if not isinstance(config_dict, dict):
            raise TypeError("'config_dict' is not a dict.")

        self.config_dict = config_dict

    def __enter__(self) -> TaskSession:
        if "lnd" in self.config_dict:
            lnclient = LNDClient(LndGrpcClient(**self.config_dict["lnd"]))
        else:
            raise ValueError("'lnd' is not included in config-file")
        ln = LightningCache(lnclient)

        if "sqlalchemy" in self.config_dict:
            db = FeelancerDB.from_config_dict(self.config_dict["sqlalchemy"]["url"])
        else:
            raise ValueError("'sqlalchemy' is not included in config-file")

        self.config = FeelancerConfig(self.config_dict)
        self.session = TaskSession(ln, db, self.config)
        return self.session

    def __exit__(self, exc_type, exc_value, traceback):
        #
        self.session.policy_updates()

        with self.session.db.session() as db_session:
            try:
                run_writer = RunWriter(self.session, db_session)
                run_writer.add_all(self.session.gen_results())
                db_session.commit()
            except Exception as e:
                db_session.rollback()
                raise e
            finally:
                db_session.close()
