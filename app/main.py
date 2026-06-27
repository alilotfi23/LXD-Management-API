"""FastAPI application entrypoint.

Wires together: structured logging, CORS, request-id middleware, startup DB
init + admin seeding, the `/api/v1` router, and global exception handlers with
a consistent JSON error shape `{"detail": ..., "request_id": ...}`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app import __version__
from app.api.v1.api import v1_router
from app.core.config import settings
from app.core.limiter import limiter
from app.db.seed import seed_admin
from app.db.session import AsyncSessionLocal, init_db
from app.utils.logging import configure_logging, request_id_ctx


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle.

    On startup we create the SQLite tables (dev convenience — Alembic is used
    for real migrations) and seed the admin user if configured.
    """
    configure_logging(settings.LOG_LEVEL)
    await init_db()
    async with AsyncSessionLocal() as db:
        await seed_admin(db)
    yield


app = FastAPI(
    title="LXD Management API",
    version=__version__,
    description=(
        "A production-ready CRUD API for managing an LXD server (instances, "
        "storage, networks, projects, images) over the LXD REST API. Supports "
        "both local Unix-socket and remote mutual-TLS connections."
    ),
    lifespan=lifespan,
)

# ---- slowapi rate limiting -------------------------------------------------
# Use the shared limiter (app.core.limiter) so `@limiter.limit` on routes and
# the app-state limiter are the same instance. Attach it to app.state and
# register the handler that turns RateLimitExceeded into HTTP 429 responses.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---- Versioned router (/api/v1) -------------------------------------------
app.include_router(v1_router, prefix="/api/v1")

# ---- CORS ------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Request-id middleware -------------------------------------------------
class RequestIdMiddleware(BaseHTTPMiddleware):
    """Stamp every request with a unique id and echo it back in a header.

    The id is stored in a contextvar so JSON log lines for that request carry
    it automatically, and so error responses can include it.
    """

    async def dispatch(self, request: StarletteRequest, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_ctx.reset(token)


app.add_middleware(RequestIdMiddleware)


# ---- Global exception handlers --------------------------------------------
# Ensure a consistent error JSON shape regardless of where the error originates.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from app.utils.logging import request_id_ctx
    import logging

    logging.getLogger("app.errors").exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": request_id_ctx.get(),
        },
    )


# ---- Health ----------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Unversioned liveness probe (cheap — does not touch LXD).

    A deeper health check that also verifies LXD connectivity is provided by
    `GET /api/v1/system/health` (added in the system step).
    """
    return {"status": "ok", "version": __version__}
