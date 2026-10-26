"""Database engine and session management.

Sessions are created per unit of work and are never shared across threads.
When the worker thread pool lands, each worker will call `session_scope()`
independently and receive its own connection from this pool -- sharing a single
session between threads is the classic failure mode this guards against.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base

settings = get_settings()

engine = create_engine(
    settings.database_url,
    # Sized for every thread that can be in the database at once: the 40
    # threads FastAPI serves requests on, the worker threads and the clock,
    # with room to spare. A pool smaller than that can be emptied by a burst
    # of calls, and the request that then waits for a connection may be the
    # one holding the duplicate check's lock.
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    # READ COMMITTED avoids the gap locking that MySQL's default
    # REPEATABLE READ applies to range scans, which would otherwise block
    # threads that are not actually contending for the same vehicle.
    isolation_level="READ COMMITTED",
    future=True,
)



@event.listens_for(engine, "connect")
def _limit_lock_waits(dbapi_connection, _connection_record) -> None:
    """Give every new connection a short row-lock wait.

    A statement that cannot get its lock in time fails with error 1205, and
    the caller handles it, instead of a thread sitting blocked on a lock that
    some other session forgot to release.
    """
    mav_cursor = dbapi_connection.cursor()
    try:
        mav_cursor.execute(
            "SET SESSION innodb_lock_wait_timeout = %s",
            (max(1, int(settings.lock_wait_timeout_seconds)),),
        )
    finally:
        mav_cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    mav_session = SessionLocal()
    try:
        yield mav_session
    except Exception:
        mav_session.rollback()
        raise
    finally:
        mav_session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with session_scope() as mav_session:
        yield mav_session


def missing_columns() -> list[str]:
    """What the code needs that the tables in MySQL do not have yet.

    Each entry is "table.column", or just "table" for a table that does not
    exist. Tables made by an older version of the code fail only when a call
    first touches the missing column, so the server checks this at start-up.
    Raises if the database cannot be reached.
    """
    mav_inspector = inspect(engine)
    mav_missing: list[str] = []
    for mav_table in Base.metadata.sorted_tables:
        if not mav_inspector.has_table(mav_table.name):
            mav_missing.append(mav_table.name)
            continue
        mav_have = {mav_column["name"] for mav_column in mav_inspector.get_columns(mav_table.name)}
        mav_missing += [
            f"{mav_table.name}.{mav_column.name}"
            for mav_column in mav_table.columns
            if mav_column.name not in mav_have
        ]
    return mav_missing
