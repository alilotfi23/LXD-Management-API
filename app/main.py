"""FastAPI application entrypoint.

This module is progressively built up across the implementation steps.
At step 1 it only exposes the unversioned health check; later steps add the
versioned `/api/v1` router (auth, instances, storage, networks, projects,
images, operations, system) and global middleware/handlers.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__

app = FastAPI(
    title="LXD Management API",
    version=__version__,
    description=(
        "A production-ready CRUD API for managing an LXD server (instances, "
        "storage, networks, projects, images) over the LXD REST API. Supports "
        "both local Unix-socket and remote mutual-TLS connections."
    ),
)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Unversioned liveness probe.

    A richer health check that also verifies LXD connectivity is added in a
    later step (`system.py`); this keeps `/health` cheap for orchestrators.
    """
    return {"status": "ok", "version": __version__}
