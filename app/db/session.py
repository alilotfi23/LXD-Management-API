"""Async database session/engine setup (SQLAlchemy 2.x + aiosqlite).

The engine is created lazily from the configured `DATABASE_URL`. We expose:

* `Base`           — declarative base for models to inherit.
* `engine`         — the async engine.
* `AsyncSessionLocal` — session factory used by the `get_db` FastAPI dependency.
* `get_db()`       — yields an `AsyncSession` for a single request.
* `init_db()`      — creates the SQLite file/tables (handy for dev & tests;
                     Alembic is used for real migrations in production).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _ensure_sqlite_dir(database_url: str) -> None:
    """For SQLite file URLs, make sure the parent directory exists."""
    if database_url.startswith("sqlite"):
        # URL looks like: sqlite+aiosqlite:///./data/lxd_api.db
        path_part = database_url.split("///")[-1]
        directory = os.path.dirname(path_part)
        if directory:
            os.makedirs(directory, exist_ok=True)


_ensure_sqlite_dir(settings.DATABASE_URL)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a scoped async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables. Used for dev/tests/first-run; production uses Alembic.

    Also imports the models so they register with `Base.metadata`.
    """
    # Import here to avoid circular imports at module load time.
    from app.db import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
