from __future__ import annotations

from typing import Type

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class FeelancerDB:
    def __init__(self, url_database: URL):
        self.engine = create_engine(url_database)
        self.session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def create_base(self, base: Type[DeclarativeBase]):
        base.metadata.create_all(bind=self.engine)

    @classmethod
    def from_config_dict(cls, config_dict: dict) -> FeelancerDB:
        return cls(URL.create(**config_dict))
