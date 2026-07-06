"""SQLAlchemy engine, session factory, and ORM models for auth + conversations.

One database (``session_db_url``) is shared with ADK's ``DatabaseSessionService``
(A1): these two application tables (``app_users``, ``conversations``) live
alongside ADK's own session tables in the same URL. There is no Alembic yet
(OQ3) — :func:`create_all` builds the two tables at startup; ADK creates its own.

The engine and session factory are created per application (stored on
``app.state`` in ``bootstrap``) rather than as module globals, so tests can spin
up isolated apps against throwaway databases.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime

from fastapi import Request
from sqlalchemy import DateTime, ForeignKey, String, create_engine, func
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


def _uuid_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base for the assistant's own (non-ADK) tables."""


class AppUser(Base):
    """A provisioned user account. Written only by the admin API (FR1, FR3)."""

    __tablename__ = "app_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Conversation(Base):
    """Conversation metadata; ``id`` *is* the AG-UI thread id and ADK session id."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[AppUser] = relationship(back_populates="conversations")


# ADK's DatabaseSessionService drives the *same* URL with an async engine, so
# session_db_url is configured with an async driver (sqlite+aiosqlite, asyncpg…).
# Our ORM is synchronous, so we swap the async driver for its sync counterpart —
# both engines then point at the same database (A1).
_ASYNC_TO_SYNC_DRIVER: dict[str, str] = {
    "sqlite+aiosqlite": "sqlite",
    "postgresql+asyncpg": "postgresql",
    "mysql+aiomysql": "mysql",
}


def to_sync_url(db_url: str) -> str:
    """Return *db_url* with any known async driver swapped for its sync driver."""
    url = make_url(db_url)
    sync_driver = _ASYNC_TO_SYNC_DRIVER.get(url.drivername)
    if sync_driver is not None:
        url = url.set(drivername=sync_driver)
    return url.render_as_string(hide_password=False)


def create_db_engine(db_url: str) -> Engine:
    """Build a synchronous SQLAlchemy engine for *db_url*.

    Accepts the configured (possibly async-driver) URL and derives the sync
    driver. ``check_same_thread=False`` is applied for SQLite so the engine is
    usable from FastAPI's threadpool workers; it is inert for other dialects.
    """
    sync_url = to_sync_url(db_url)
    connect_args: dict[str, object] = {}
    if sync_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(sync_url, connect_args=connect_args, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a ``sessionmaker`` bound to *engine*."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_all(engine: Engine) -> None:
    """Create the ``app_users`` and ``conversations`` tables if absent (OQ3)."""
    Base.metadata.create_all(engine)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped ORM session.

    Reads the per-app session factory that ``bootstrap`` stores on
    ``app.state.db_sessionmaker``.
    """
    factory: sessionmaker[Session] = request.app.state.db_sessionmaker
    db = factory()
    try:
        yield db
    finally:
        db.close()
