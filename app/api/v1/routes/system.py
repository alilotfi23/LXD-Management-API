"""System endpoints — LXD server info, host resources, and deep health check.

Maps to LXD REST endpoints (under ``/1.0``):

  GET /                           -> server info (version, auth, addresses)
  GET /resources                  -> host CPU, memory, storage, GPU, network
  GET /storage-pools/{name}/resources -> pool-specific resource usage

Also provides ``GET /api/v1/system/health`` — a *deep* health check that
verifies LXD connectivity in addition to basic API liveness. The unversioned
``GET /health`` stays cheap (no LXD call); this one returns 503 if LXD is
unreachable.

RBAC: all endpoints require viewer+.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, LXDClientDep, ProjectParam, RequireViewer
from app.services.exceptions import LXDConnectionError, LXDError
from app.utils.logging import request_id_ctx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


def _lxd_err_to_http(exc: LXDError) -> HTTPException:
    code_map = {
        "LXDBadRequest": 400,
        "LXDAuthError": 403,
        "LXDNotFoundError": 404,
        "LXDConflictError": 409,
        "LXDConnectionError": 503,
    }
    http_code = code_map.get(type(exc).__name__, exc.status_code or 500)
    return HTTPException(status_code=http_code, detail=exc.message)


@router.get("/health", summary="Deep health check (includes LXD connectivity)")
async def health_deep(
    lxd: LXDClientDep,
) -> dict[str, Any]:
    """Check both the API itself and connectivity to LXD.

    Returns 200 if LXD is reachable; 503 if LXD is down or the connection
    cannot be established (socket missing / TLS failure / timeout).
    """
    try:
        # A lightweight call to verify the connection works.
        await lxd.get("/")
    except LXDConnectionError as exc:
        return {
            "status": "unhealthy",
            "lxd": "unreachable",
            "detail": exc.message,
            "request_id": request_id_ctx.get(),
        }
    except LXDError as exc:
        # Auth or other non-connection errors still mean LXD is reachable.
        return {
            "status": "degraded",
            "lxd": "reachable",
            "detail": exc.message,
            "request_id": request_id_ctx.get(),
        }
    return {
        "status": "healthy",
        "lxd": "reachable",
        "request_id": request_id_ctx.get(),
    }


@router.get("/info", summary="LXD server info (version, auth, addresses)")
async def server_info(
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0``.

    Returns the server environment: LXD version, supported APIs,
    authentication methods, addresses, and more.
    """
    try:
        return await lxd.get(
            "/", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.get("/resources", summary="Host resource info (CPU, memory, storage, network)")
async def host_resources(
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/resources``.

    Returns a snapshot of the host's hardware: CPU topology, memory size,
    PCI/USB devices, GPU info, network interfaces, and storage disks.
    """
    try:
        return await lxd.get(
            "resources", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.get(
    "/storage-pools/{name}/resources",
    summary="Storage pool resource usage",
)
async def pool_resources(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/storage-pools/{name}/resources``.

    Returns the pool's space usage, inodes, and (for ZFS) dataset-specific
    information.
    """
    try:
        return await lxd.get(
            f"storage-pools/{name}/resources",
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


__all__ = ["router"]
