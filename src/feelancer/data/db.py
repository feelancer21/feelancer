from __future__ import annotations

import logging
import time
from typing import Callable, Type, TypeVar

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

T = TypeVar("T")
V = TypeVar("V")

MAX_EXECUTIONS = 5
DELAY = 5


class FeelancerDB:
    def __init__(self, url_database: URL):
        self.engine = create_engine(url_database)
        self.session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def create_base(self, base: Type[DeclarativeBase]):
        base.metadata.create_all(bind=self.engine)

    @classmethod
    def from_config_dict(cls, config_dict: dict) -> FeelancerDB:
        return cls(URL.create(**config_dict))

    def execute(self, func: Callable[[Session], T]) -> T:
        """
        Executes a callable in session. If it fails we repeat the execution
        multiple times.
        """
        return self._execute(func, None)

    def execute_post(self, func: Callable[[Session], V], post: Callable[[V], T]) -> T:
        """
        Executes the callable 'func' in session before a database commit. After
        the commit the 'post' function is applied on the result in the session.
        If it fails we repeat the execution multiple times.
        """
        return self._execute(func, post)

    def _execute(
        self,
        pre_commit: Callable[[Session], V],
        post_commit: Callable[[V], T] | None,
    ) -> T:
        """
        The main executor for database operations.
        """

        ex = Exception("Undefined error during database execution occurred.")

        for r in range(MAX_EXECUTIONS):
            needs_commit = False

            with self.session() as session:
                try:
                    """
                    We execute the pre_commit function in the session, check
                    if a commit is needed and eventually we process the post_commit
                    function on the result after the commit.
                    """
                    res = pre_commit(session)

                    if session.new or session.dirty or session.deleted:
                        # storing the information for rollback
                        needs_commit = True
                        session.commit()

                    if post_commit is None:
                        return res  # type: ignore
                    return post_commit(res)

                except Exception as e:
                    if needs_commit:
                        session.rollback()

                    self.engine.dispose()
                    ex = e

                finally:
                    session.close()

            logging.warning(
                f"Error occurred during database operation; "
                f"Starting retry {r+1} in {DELAY}s ..."
            )
            time.sleep(DELAY)

        logging.error(f"Maximum number of retries {MAX_EXECUTIONS} exceeded.")

        raise ex
