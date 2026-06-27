"""Project routes — LXD's namespace/isolation feature.

Maps to LXD REST endpoints (under ``/1.0``):

  GET    /projects                     -> list projects
  POST   /projects                     -> create project (async)
  GET    /projects/{name}              -> get project
  PUT    /projects/{name}              -> update project (async)
  PATCH  /projects/{name}              -> patch project (async)
  DELETE /projects/{name}              -> delete project (async)

All other resource endpoints in the API accept an optional ``?project=``
query param (or ``X-LXD-Project`` header) that scopes the request to a
specific LXD project. See `app/api/deps.py:project_param`.

RBAC: GET -> viewer+; all mutations -> admin only (projects are infra).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import (
    CurrentUser,
    LXDClientDep,
    RequireAdmin,
    RequireViewer,
)
from app.schemas.projects import ProjectCreate, ProjectPatch, ProjectUpdate
from app.services.exceptions import LXDError
from app.services.lxd_operations import to_async_ref
from app.utils.pagination import PageParams, page_params, paginate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


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


@router.get("", summary="List LXD projects")
async def list_projects(
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    page: PageParams = Depends(page_params),
    expand: bool = Query(True),
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/projects?recursion=1``."""
    try:
        result = await lxd.get(
            "projects", params={"recursion": 1 if expand else 0}
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    items = result if isinstance(result, list) else []
    return paginate(items, page)


@router.post("", status_code=202, summary="Create a project (async)")
async def create_project(
    payload: ProjectCreate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
) -> dict[str, Any]:
    """Maps to LXD ``POST /1.0/projects`` (async op)."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.post("projects", json_body=body)
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.get("/{name}", summary="Get a project")
async def get_project(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
) -> Any:
    """Maps to LXD ``GET /1.0/projects/{name}``."""
    try:
        return await lxd.get(f"projects/{name}")
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.put("/{name}", status_code=202, summary="Update a project (async)")
async def update_project(
    name: str,
    payload: ProjectUpdate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
) -> dict[str, Any]:
    """Maps to LXD ``PUT /1.0/projects/{name}``."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.put(f"projects/{name}", json_body=body)
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.patch("/{name}", status_code=202, summary="Patch a project (async)")
async def patch_project(
    name: str,
    payload: ProjectPatch,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
) -> dict[str, Any]:
    """Maps to LXD ``PATCH /1.0/projects/{name}``."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.patch(f"projects/{name}", json_body=body)
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.delete("/{name}", status_code=202, summary="Delete a project (async)")
async def delete_project(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
) -> dict[str, Any]:
    """Maps to LXD ``DELETE /1.0/projects/{name}``."""
    try:
        result = await lxd.delete(f"projects/{name}")
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


__all__ = ["router"]
