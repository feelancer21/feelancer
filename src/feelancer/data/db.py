from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable, Generator, Sequence, Type, TypeVar

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

T = TypeVar("T")
V = TypeVar("V")
W = TypeVar("W")

MAX_EXECUTIONS = 5
DELAY = 5

if TYPE_CHECKING:
    from sqlalchemy import Select


def _fields_to_dict(result, relations: dict[str, dict]) -> dict:
    """
    Transforms all columns of a result to dictionary.
    """

    res = {}
    res |= {col: getattr(result, col) for col in result.__table__.columns.keys()}

    if len(relations) == 0:
        return res

    for rel_name in relations.keys():
        res |= _fields_to_dict(getattr(result, rel_name), relations[rel_name])
    return res


def _explore_path(path: Sequence, rel_dict: dict[str, dict]) -> dict[str, dict]:
    """Explores an option.path of a query recursively."""

    if len(path) > 1:
        step_name = str(path[1]).split(".")[1]
        if not rel_dict.get(step_name):
            rel_dict[step_name] = {}
        rel_dict[step_name] |= _explore_path(path[2:], rel_dict[step_name])

    return rel_dict


def _create_dict_gen_call(
    qry: Select[tuple[T]],
) -> Callable[[Sequence], Generator[dict, None, None]]:
    """
    Given a query, this function returns a callable which generates dictionaries
    resolving all fields including the joinedload data.
    """

    # First step is exploring the joined relationships in the query. Each
    # relationship with loaded data gets a key in the dict relations.
    # The value is a dict with its relations as value. If the value is an empty
    # dict then there is nothing more to resolve.
    # If it is not empty one can further to explore the next relations.
    relations: dict[str, dict] = {}

    # We are looping over all options. Each option.path is a Sequence and each
    # second entry of this sequence is relationship we'd like to explore.
    for o in qry._with_options:
        path: Sequence = o.path  # type: ignore
        relations |= _explore_path(path, relations)

    def func(result: Sequence[T]) -> Generator[dict, None, None]:
        for r in result:
            yield _fields_to_dict(r, relations)

    return func


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

                    # We execute the pre_commit function in the session, check
                    # if a commit is needed and eventually we process the
                    # post_commit function on the result after the commit.

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

    def query_all_to_list(
        self, qry: Select[tuple[T]], convert: Callable[[T], V]
    ) -> list[V]:
        """
        Executes qry query. Each element of the result is converted by the
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
        self, qry: Select[tuple[T]], key: Callable[[T], V], value: Callable[[T], W]
    ) -> dict[V, W]:
        """
        Executes the query. Each element of the result is stored in a dict.
        For deriving key and value the identical named callbacks are used.
        """

        # Callback which executes the query and returns the results as ORM objects
        def get_data(session: Session) -> Sequence[T]:
            return session.execute(qry).scalars().all()

        # Callback for creating the dict with a dict comprehension
        def to_dict(result: Sequence[T]) -> dict[V, W]:
            return {key(r): value(r) for r in result}

        return self._execute(get_data, to_dict)

    def qry_all_to_field_dict_gen(
        self, qry: Select[tuple[T]]
    ) -> Generator[dict, None, None]:
        """
        Executes the query and returns a generator of dictionaries. Each dict
        contains all fields as key value pairs, including the joined load data.
        """

        # Callback which executes the query and returns the results as ORM objects
        def get_data(session: Session) -> Sequence[T]:
            return session.execute(qry).scalars().all()

        return self._execute(get_data, _create_dict_gen_call(qry))

    def query_first(
        self, qry: Select[tuple[T]], convert: Callable[[T], V], default: W = None
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

    def query_all_to_list(
        self, qry: Select[tuple[T]], convert: Callable[[T], V]
    ) -> list[V]:
        """
        Executes the query in this session. Returns a list with the converted
        results.

        """
        return [convert(r) for r in self.session.execute(qry).scalars().all()]

    def query_all_to_dict(
        self, qry: Select[tuple[T]], key: Callable[[T], V], value: Callable[[T], W]
    ) -> dict[V, W]:
        """
        Executes the query in this session. Returns a dict with the converted
        results.
        """
        return {key(r): value(r) for r in self.session.execute(qry).scalars().all()}

    def query_first(
        self, qry: Select[tuple[T]], convert: Callable[[T], V], default: W = None
    ) -> V | W:
        """
        Returns the conversion with the callback of the first element of the query.
        If the first element is None, the default value is returned.
        """

        res = self.session.execute(qry).scalars().first()
        if not res:
            return default
        return convert(res)
