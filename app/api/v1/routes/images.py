"""Image routes — list, get, delete, copy/import from remote.

Maps to LXD REST endpoints (under ``/1.0``):

  GET    /images                        -> list local images
  GET    /images/{fingerprint}           -> get image details
  DELETE /images/{fingerprint}           -> delete an image
  POST   /images                        -> copy from remote / import (async op)
  POST   /images/aliases                 -> create an alias
  DELETE /images/aliases/{name}         -> delete an alias

Copying an image from a remote server (e.g. ``images:ubuntu/22.04``) is a
potentially long-running download. LXD returns an async operation that the
client should poll/wait/subscribe to.

RBAC: GET -> viewer+; create/copy/delete -> admin.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import (
    CurrentUser,
    LXDClientDep,
    ProjectParam,
    RequireAdmin,
    RequireViewer,
)
from app.schemas.images import ImageAliasCreate, ImageCreate
from app.services.exceptions import LXDError
from app.services.lxd_operations import to_async_ref
from app.utils.pagination import PageParams, page_params, paginate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])


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


@router.get("", summary="List local images")
async def list_images(
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
    page: PageParams = Depends(page_params),
    expand: bool = Query(True),
    filter: Optional[str] = Query(None, description="LXD OData filter."),
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/images?recursion=1``."""
    params: dict[str, Any] = {
        "recursion": 1 if expand else 0,
        "project": project,
    }
    if filter:
        params["filter"] = filter
    try:
        result = await lxd.get("images", params=params)
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    items = result if isinstance(result, list) else []
    return paginate(items, page)


@router.get("/{fingerprint}", summary="Get image details")
async def get_image(
    fingerprint: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/images/{fingerprint}``."""
    try:
        return await lxd.get(
            f"images/{fingerprint}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.delete(
    "/{fingerprint}", status_code=202, summary="Delete a local image (async)"
)
async def delete_image(
    fingerprint: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``DELETE /1.0/images/{fingerprint}``."""
    try:
        result = await lxd.delete(
            f"images/{fingerprint}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.post(
    "", status_code=202, summary="Copy/import an image (async)"
)
async def create_image(
    payload: ImageCreate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Copy an image from a remote server or import locally.

    Maps to LXD ``POST /1.0/images``. Image copying/downloading is a
    long-running async operation; the response includes an operation
    reference for polling/waiting.

    Example body to copy from the default Ubuntu image server::

        {
            "source": {
                "type": "image",
                "alias": "ubuntu/22.04",
                "server": "https://images.linuxcontainers.org",
                "protocol": "simplestreams"
            },
            "auto_update": true
        }
    """
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.post(
            "images", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.post("/aliases", status_code=201, summary="Create an image alias")
async def create_alias(
    payload: ImageAliasCreate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``POST /1.0/images/aliases``."""
    body = payload.model_dump(exclude_none=True)
    try:
        return await lxd.post(
            "images/aliases", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.delete("/aliases/{name}", status_code=202, summary="Delete an image alias")
async def delete_alias(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
) -> dict[str, Any]:
    """Maps to LXD ``DELETE /1.0/images/aliases/{name}``."""
    try:
        result = await lxd.delete(f"images/aliases/{name}")
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


__all__ = ["router"]
