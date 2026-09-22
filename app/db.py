"""Database engine and session management.

Sessions are created per unit of work and are never shared across threads.
When the worker thread pool lands, each worker will call `session_scope()`
independently and receive its own connection from this pool -- sharing a single
session between threads is the classic failure mode this guards against.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    # READ COMMITTED avoids the gap locking that MySQL's default
    # REPEATABLE READ applies to range scans, which would otherwise block
    # threads that are not actually contending for the same vehicle.
    isolation_level="READ COMMITTED",
    future=True,
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with session_scope() as session:
        yield session
