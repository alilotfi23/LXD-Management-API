"""Shared pytest fixtures.

Strategy:
  * Use an in-memory SQLite database (file:// would also work) created fresh
    for each test via the async engine + `init_db`.
  * Override the `get_db` dependency so the app uses the test session.
  * Provide a **mocked LXD client** that records calls and returns canned
    LXD-style responses, so no real LXD daemon is needed. We override the
    `get_lxd_client` dependency with this mock.
  * Provide a ready-made admin JWT for protected endpoints.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import deps
from app.core.security import Role, create_access_token, hash_password
from app.db import crud
from app.db.models import User
from app.db.session import Base, get_db
from app.main import app
from app.services.lxd_client import LXDClient


# ---------------------------------------------------------------------------
# Pytest configuration
# ---------------------------------------------------------------------------
def pytest_configure(config):
    """Register asyncio mode + custom markers."""
    config.addinivalue_line("markers", "lxd: LXD REST API integration test")


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the whole session (async fixtures)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def db_engine():
    """A fresh in-memory SQLite engine per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    """An async session bound to the in-memory engine."""
    sessionmaker = async_sessionmaker(
        bind=db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessionmaker() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def _override_get_db(db_session):
    """Override the app's get_db dependency to use the test session."""

    async def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[deps.get_db] = _get_db_override
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mocked LXD client fixture
# ---------------------------------------------------------------------------
class MockLXDClient:
    """A controllable mock that mimics the LXDClient surface used by routes.

    Each method is an ``AsyncMock`` so tests can assert on call args and
    configure return values / side effects. ``_responses`` lets a test stage
    a canned envelope per (method, path) for ``request``.
    """

    def __init__(self) -> None:
        self.get = AsyncMock(name="get")
        self.post = AsyncMock(name="post")
        self.put = AsyncMock(name="put")
        self.patch = AsyncMock(name="patch")
        self.delete = AsyncMock(name="delete")
        self.request = AsyncMock(name="request")
        self.ws_url = MagicMock(name="ws_url", return_value="ws://mock/events")
        self.aclose = AsyncMock(name="aclose")
        # Sensible defaults so list endpoints return empty without setup.
        self.get.return_value = []


@pytest.fixture
def mock_lxd() -> MockLXDClient:
    """Provide a fresh MockLXDClient and override the dependency."""
    client = MockLXDClient()
    app.dependency_overrides[deps.get_lxd_client] = lambda: client
    yield client
    app.dependency_overrides.pop(deps.get_lxd_client, None)


# ---------------------------------------------------------------------------
# User + auth fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def admin_user(db_session) -> User:
    """Create an admin user directly in the DB."""
    return await crud.create_user(
        db_session, username="admin", password="password123", role=Role.ADMIN.value
    )


@pytest_asyncio.fixture
async def viewer_user(db_session) -> User:
    return await crud.create_user(
        db_session, username="viewer", password="password123", role=Role.VIEWER.value
    )


@pytest.fixture
def admin_token(admin_user) -> str:
    """A valid admin access JWT."""
    return create_access_token(admin_user.username, role=admin_user.role)


@pytest.fixture
def viewer_token(viewer_user) -> str:
    """A valid viewer access JWT."""
    return create_access_token(viewer_user.username, role=viewer_user.role)


# ---------------------------------------------------------------------------
# HTTP client fixture
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client():
    """An async HTTP client wired to the FastAPI app via ASGI transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def admin_headers(admin_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def viewer_headers(viewer_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {viewer_token}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def lxd_sync(metadata: Any) -> dict[str, Any]:
    """Build a canonical LXD sync envelope (type: sync)."""
    return {"type": "sync", "status": "Success", "status_code": 200, "metadata": metadata}


def lxd_async(operation_id: str = "op-123") -> dict[str, Any]:
    """Build a canonical LXD async envelope (type: async, HTTP 202)."""
    return {
        "type": "async",
        "status": "Operation created",
        "status_code": 202,
        "operation": f"/1.0/operations/{operation_id}",
        "metadata": {
            "id": operation_id,
            "class": "task",
            "status": "Running",
            "status_code": 103,
        },
    }


def lxd_error(error: str, code: int = 404) -> dict[str, Any]:
    """Build a canonical LXD error envelope (type: error)."""
    return {"type": "error", "error": error, "error_code": code}
