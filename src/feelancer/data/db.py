from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable, Sequence, Type, TypeVar

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

T = TypeVar("T")
V = TypeVar("V")
W = TypeVar("W")

MAX_EXECUTIONS = 5
DELAY = 5

if TYPE_CHECKING:
    from sqlalchemy.orm import Query


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

    def query_all_to_list(self, qry: Query[T], convert: Callable[[T], V]) -> list[V]:
        """
        Executes the qry Query. Each element of the result is converted by the
        provided function 'convert' and stored in a list afterwards.
        """

        # Callback which executes the query and returns the results as ORM objects
        def get_data(session: Session) -> Sequence[T]:
            return session.execute(qry).scalars().all()

        # Callback for creating a list with a list comprehension
        def to_list(result: Sequence[T]) -> list[V]:
            return [convert(r) for r in result]

        return self._execute(get_data, to_list)

    def query_all_to_dict(
        self, qry: Query[T], key: Callable[[T], V], value: Callable[[T], W]
    ) -> dict[V, W]:
        """
        Executes the qry Query. Each element of the result is stored in a dict.
        For deriving key and value the identical named callbacks are used.
        """

        # Callback which executes the query and returns the results as ORM objects
        def get_data(session: Session) -> Sequence[T]:
            return session.execute(qry).scalars().all()

        # Callback for creating the dict with a dict comprehension
        def to_dict(result: Sequence[T]) -> dict[V, W]:
            return {key(r): value(r) for r in result}

        return self._execute(get_data, to_dict)

    def query_first(
        self, qry: Query[T], convert: Callable[[T], V], default: W = None
    ) -> V | W:
        """
        Returns the conversion with the callback of the first element of the query.
        If the first element is None, the default value is returned.
        """

        # Callback which executes the query and returns a ORM objects or None
        def get_data(session: Session) -> T | None:
            return session.execute(qry).scalars().first()

        # Conversion function considering the default for the case the result is None
        def convert_default(result: T | None) -> V | W:
            if not result:
                return default
            return convert(result)

        return self._execute(get_data, convert_default)


class SessionExecutor:
    """
    For executing queries in an existing session and transforming the data in a
    target format.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def query_all_to_list(self, qry: Query[T], convert: Callable[[T], V]) -> list[V]:
        """
        Executes the query in this session. Returns a list with the converted
        results.

        """
        return [convert(r) for r in self.session.execute(qry).scalars().all()]

    def query_all_to_dict(
        self, qry: Query[T], key: Callable[[T], V], value: Callable[[T], W]
    ) -> dict[V, W]:
        """
        Executes the query in this session. Returns a dict with the converted
        results.
        """
        return {key(r): value(r) for r in self.session.execute(qry).scalars().all()}

    def query_first(
        self, qry: Query[T], convert: Callable[[T], V], default: W = None
    ) -> V | W:
        """
        Returns the conversion with the callback of the first element of the query.
        If the first element is None, the default value is returned.
        """

        res = self.session.execute(qry).scalars().first()
        if not res:
            return default
        return convert(res)
