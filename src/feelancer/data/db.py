from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Callable, Generator, Iterable, Sequence
from itertools import batched
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import URL, Row, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from feelancer.log import getLogger
from feelancer.retry import create_retry_handler

if TYPE_CHECKING:
    from sqlalchemy import Delete, Select
T = TypeVar("T")
V = TypeVar("V")
W = TypeVar("W")

EXCEPTIONS_RETRY = (Exception,)
EXCEPTIONS_RAISE = (IntegrityError,)
MAX_RETRIES = 5
DELAY = 5
MIN_TOLERANCE_DELTA = 60


logger = getLogger(__name__)


class GetIdException(Exception): ...


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
) -> Callable[[Sequence], Generator[dict]]:
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

    def func(result: Sequence[T]) -> Generator[dict]:
        for r in result:
            yield _fields_to_dict(r, relations)

    return func


# Retry handler for database operations. We are raising IntegrityError amd retrying
# on all other exceptions.
_retry_handler = create_retry_handler(
    exceptions_retry=EXCEPTIONS_RETRY,
    exceptions_raise=EXCEPTIONS_RAISE,
    max_retries=MAX_RETRIES,
    delay=DELAY,
    min_tolerance_delta=MIN_TOLERANCE_DELTA,
)


class FeelancerDB:
    def __init__(self, url_database: URL):
        self.engine = create_engine(url_database)
        self.session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def create_base(self, base: type[DeclarativeBase]):
        base.metadata.create_all(bind=self.engine)

    @classmethod
    def from_config_dict(cls, config_dict: dict) -> FeelancerDB:
        return cls(URL.create(**config_dict))

    def execute(self, func: Callable[[Session], T]) -> T:
        """
        Executes a callable in session. If it fails we repeat the execution
        multiple times.
        """
        return self._execute(func)

    def execute_post(self, func: Callable[[Session], V], post: Callable[[V], T]) -> T:
        """
        Executes the callable 'func' in session before a database commit. After
        the commit the 'post' function is applied on the result in the session.
        If it fails we repeat the execution multiple times.
        """
        return self._execute(func, post)

    @_retry_handler
    def _execute(
        self,
        pre_commit: Callable[[Session], V],
        post_commit: Callable[[V], T] = lambda x: x,
        needs_commit: bool = False,
    ) -> T:
        """
        The main executor for database operations.
        """

        with self.session() as session:
            try:
                # We execute the pre_commit function in the session, check
                # if a commit is needed and eventually we process the
                # post_commit function on the result after the commit.
                res = pre_commit(session)

                # If we are using sqlqlchemy's ORM we need to check if the session
                # is dirty, new or deleted. If so we need to commit the session
                # and eventually rollback if an exception occurs.
                # In case of Core API the flag has to provided by the caller.
                if session.new or session.dirty or session.deleted:
                    needs_commit = True

                if needs_commit:
                    session.commit()

                return post_commit(res)

            except Exception as e:
                if needs_commit:
                    session.rollback()

                raise e

    def new_get_id_or_add(
        self,
        get_qry: Callable[[V], Select[tuple[T]]],
        read_id: Callable[[T], int],
    ) -> Callable[[V, Callable[[], T] | None], int]:
        """
        Returns a closure which can be used to get the id of an object or add it
        to the database if it does not exist.
        """

        def get_id(qry: Select[tuple[T]]) -> int:
            # Execute the query and reads the id of the first result.
            if (id := self.sel_first(qry, read_id)) is None:
                raise GetIdException()
            return id

        def get_id_or_add(key: V, get_new_obj: Callable[[], T] | None) -> int:
            # We test if the object exists in the database. If it does not exist
            # we create a new object and add it to the database, if get_new_obj
            # is set.

            qry = get_qry(key)
            try:
                return get_id(qry)
            except GetIdException as e:
                if get_new_obj is None:
                    raise e

                # If the object does not exist we add a new one. It there is
                # was race with another thread, an IntegrityError is raised.
                try:
                    return self.add_post(get_new_obj(), read_id)
                except IntegrityError:
                    return get_id(qry)

        return get_id_or_add

    def sel_all_to_list(
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

    def sel_all_to_dict(
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

    def sel_all_to_field_dict_gen(self, qry: Select[tuple[T]]) -> Generator[dict]:
        """
        Executes the query and returns a generator of dictionaries. Each dict
        contains all fields as key value pairs, including the joined load data.
        """

        # Callback which executes the query and returns the results as ORM objects
        def get_data(session: Session) -> Sequence[T]:
            return session.execute(qry).scalars().all()

        return self._execute(get_data, _create_dict_gen_call(qry))

    def sel_all_to_csv(
        self,
        qry: Select[tuple[T, ...]],
        file_path: str,
        header: list[str] | None = None,
    ) -> None:
        """
        Executes the query and writes the result to a csv file.
        """

        def get_data(session: Session) -> Sequence[Row[tuple[T, ...]]]:
            return session.execute(qry).all()

        result: Sequence[Row[tuple[T, ...]]] = self.execute(get_data)

        path = os.path.expanduser(file_path)

        # Write the result to a temporary file first.
        dir_name = os.path.dirname(path)
        with tempfile.NamedTemporaryFile(
            "w", newline="", dir=dir_name, delete=False
        ) as tmp_file:
            temp_path = tmp_file.name
            writer = csv.writer(tmp_file)
            if header is not None:
                writer.writerow(header)
            for row in result:
                writer.writerow(row)

        # Atomically move the temporary file to the final destination
        os.replace(temp_path, path)

    def sel_first(
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

    def add(self, data: DeclarativeBase, accept_integrity_err: bool = False) -> None:
        """
        Adds the data to the database.
        """

        try:
            self.execute(lambda session: session.add(data))
        except Exception as e:
            logger.error(f"Error while adding data to db: {e}")

            if accept_integrity_err and isinstance(e, IntegrityError):
                return

            raise e

    def add_post(self, data: T, post: Callable[[T], V]) -> V:
        """
        Adds the data to the database and executes the post function on the result.
        """

        def add_data(session: Session) -> T:
            session.add(data)
            return data

        return self.execute_post(add_data, post)

    def add_all_from_iterable(
        self, iter: Iterable[DeclarativeBase], accept_integrity_err: bool = False
    ) -> None:
        """
        Adds all data from the iterable to the database.
        """
        for i in iter:
            self.add(i, accept_integrity_err)

    def add_chunks_from_iterable(
        self, iter: Iterable[DeclarativeBase], chunk_size: int
    ) -> None:
        """
        Adds all data from the iterable to the database in chunks.
        """

        for chunk in batched(iter, chunk_size):
            self.execute(lambda session: session.add_all(chunk))

    def del_core(self, queries: Iterable[Delete[tuple]] | Delete[tuple]) -> None:
        """
        Deletes all data from the database using the core API. One can provide
        a single query or an iterable of queries. In the latter case all queries
        are executed in the same session.
        """

        def delete_data(session: Session) -> None:
            # Case if one query is provided
            if not isinstance(queries, Iterable):
                session.execute(queries)
                return

            for q in queries:
                session.execute(q)

        self._execute(pre_commit=delete_data, needs_commit=True)


class SessionExecutor:
    """
    For executing queries in an existing session and transforming the data in a
    target format.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def sel_all_to_list(
        self, qry: Select[tuple[T]], convert: Callable[[T], V]
    ) -> list[V]:
        """
        Executes the query in this session. Returns a list with the converted
        results.

        """
        return [convert(r) for r in self.session.execute(qry).scalars().all()]

    def sel_all_to_dict(
        self, qry: Select[tuple[T]], key: Callable[[T], V], value: Callable[[T], W]
    ) -> dict[V, W]:
        """
        Executes the query in this session. Returns a dict with the converted
        results.
        """
        return {key(r): value(r) for r in self.session.execute(qry).scalars().all()}

    def sel_first(
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
