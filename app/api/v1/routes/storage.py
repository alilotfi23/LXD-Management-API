"""Storage pool and volume routes.

Maps to LXD REST endpoints (under ``/1.0``):

  **Pools:**
    GET    /storage-pools                        -> list pools
    POST   /storage-pools                        -> create pool (async)
    GET    /storage-pools/{name}                 -> get pool
    PUT    /storage-pools/{name}                 -> update pool (async)
    PATCH  /storage-pools/{name}                 -> patch pool (async)
    DELETE /storage-pools/{name}                 -> delete pool (async)

  **Volumes (within a pool):**
    GET    /storage-pools/{pool}/volumes                -> list volumes
    POST   /storage-pools/{pool}/volumes                -> create volume (async)
    GET    /storage-pools/{pool}/volumes/{type}/{name}  -> get volume
    PUT    /storage-pools/{pool}/volumes/{type}/{name}  -> update volume (async)
    PATCH  /storage-pools/{pool}/volumes/{type}/{name}  -> patch volume (async)
    DELETE /storage-pools/{pool}/volumes/{type}/{name}  -> delete volume (async)

  **Volume attach/detach:**
    These are not direct LXD REST endpoints — LXD attaches volumes by PATCHing
    the instance's devices list with a disk device pointing at pool+volume.
    We expose it as dedicated convenience endpoints.

RBAC:
  - GET (pools/volumes)    -> viewer+
  - create/update/delete    -> admin (infra-level)
  - attach/detach           -> operator+
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
    RequireOperator,
    RequireViewer,
)
from app.schemas.storage import (
    StoragePoolCreate,
    StoragePoolPatch,
    StoragePoolUpdate,
    StorageVolumeCreate,
    StorageVolumeUpdate,
    VolumeAttachDetach,
)
from app.services.exceptions import LXDError
from app.services.lxd_operations import to_async_ref
from app.utils.pagination import PageParams, page_params, paginate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storage", tags=["storage"])


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


# ============================================================
# Storage Pools
# ============================================================

@router.get("/pools", summary="List storage pools")
async def list_pools(
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
    page: PageParams = Depends(page_params),
    expand: bool = Query(True),
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/storage-pools?recursion=1``."""
    try:
        result = await lxd.get(
            "storage-pools",
            params={"recursion": 1 if expand else 0, "project": project},
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    items = result if isinstance(result, list) else []
    return paginate(items, page)


@router.post("/pools", status_code=202, summary="Create a storage pool (async)")
async def create_pool(
    payload: StoragePoolCreate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``POST /1.0/storage-pools`` (async op)."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.post(
            "storage-pools", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.get("/pools/{name}", summary="Get a storage pool")
async def get_pool(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/storage-pools/{name}``."""
    try:
        return await lxd.get(
            f"storage-pools/{name}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.put("/pools/{name}", status_code=202, summary="Update a storage pool (async)")
async def update_pool(
    name: str,
    payload: StoragePoolUpdate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``PUT /1.0/storage-pools/{name}``."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.put(
            f"storage-pools/{name}", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.patch("/pools/{name}", status_code=202, summary="Patch a storage pool (async)")
async def patch_pool(
    name: str,
    payload: StoragePoolPatch,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``PATCH /1.0/storage-pools/{name}``."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.patch(
            f"storage-pools/{name}", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.delete("/pools/{name}", status_code=202, summary="Delete a storage pool (async)")
async def delete_pool(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``DELETE /1.0/storage-pools/{name}``."""
    try:
        result = await lxd.delete(
            f"storage-pools/{name}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


# ============================================================
# Storage Volumes (within a pool)
# ============================================================

@router.get(
    "/pools/{pool}/volumes",
    summary="List volumes in a pool",
)
async def list_volumes(
    pool: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
    volume_type: Optional[str] = Query(None, alias="type"),
    page: PageParams = Depends(page_params),
    expand: bool = Query(True),
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/storage-pools/{pool}/volumes?recursion=1``."""
    params: dict[str, Any] = {"recursion": 1 if expand else 0, "project": project}
    if volume_type:
        params["type"] = volume_type
    try:
        result = await lxd.get(f"storage-pools/{pool}/volumes", params=params)
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    items = result if isinstance(result, list) else []
    return paginate(items, page)


@router.post(
    "/pools/{pool}/volumes",
    status_code=202,
    summary="Create a storage volume (async)",
)
async def create_volume(
    pool: str,
    payload: StorageVolumeCreate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``POST /1.0/storage-pools/{pool}/volumes`` (async op)."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.post(
            f"storage-pools/{pool}/volumes",
            json_body=body,
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.get(
    "/pools/{pool}/volumes/{volume_type}/{name}",
    summary="Get a storage volume",
)
async def get_volume(
    pool: str,
    volume_type: str,
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/storage-pools/{pool}/volumes/{type}/{name}``."""
    try:
        return await lxd.get(
            f"storage-pools/{pool}/volumes/{volume_type}/{name}",
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.put(
    "/pools/{pool}/volumes/{volume_type}/{name}",
    status_code=202,
    summary="Update a storage volume (async)",
)
async def update_volume(
    pool: str,
    volume_type: str,
    name: str,
    payload: StorageVolumeUpdate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``PUT /1.0/storage-pools/{pool}/volumes/{type}/{name}``."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.put(
            f"storage-pools/{pool}/volumes/{volume_type}/{name}",
            json_body=body,
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.patch(
    "/pools/{pool}/volumes/{volume_type}/{name}",
    status_code=202,
    summary="Patch a storage volume (async)",
)
async def patch_volume(
    pool: str,
    volume_type: str,
    name: str,
    payload: dict,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``PATCH /1.0/storage-pools/{pool}/volumes/{type}/{name}``."""
    try:
        result = await lxd.patch(
            f"storage-pools/{pool}/volumes/{volume_type}/{name}",
            json_body=payload,
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.delete(
    "/pools/{pool}/volumes/{volume_type}/{name}",
    status_code=202,
    summary="Delete a storage volume (async)",
)
async def delete_volume(
    pool: str,
    volume_type: str,
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``DELETE /1.0/storage-pools/{pool}/volumes/{type}/{name}``."""
    try:
        result = await lxd.delete(
            f"storage-pools/{pool}/volumes/{volume_type}/{name}",
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


# ============================================================
# Volume attach / detach (convenience over instance PATCH)
# ============================================================

@router.post(
    "/pools/{pool}/volumes/custom/{name}/attach",
    status_code=202,
    summary="Attach a custom volume to an instance (async)",
)
async def attach_volume(
    pool: str,
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    instance: str = Query(..., description="Instance name to attach to."),
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Attach a custom volume to an instance.

    LXD has no dedicated attach endpoint — we PATCH the instance's devices to
    add a disk device ``{"pool": "<pool>", "volume": "<name>", "path": "..."}``.
    Maps to LXD ``PATCH /1.0/instances/{instance}``.
    """
    # Fetch the instance first to preserve existing devices.
    try:
        inst = await lxd.get(
            f"instances/{instance}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc

    devices = {}
    if isinstance(inst, dict) and "devices" in inst:
        devices = dict(inst["devices"])

    # Add the disk device pointing at the custom volume.
    device_key = f"vol_{name}"
    devices[device_key] = {
        "type": "disk",
        "pool": pool,
        "volume": name,
        "path": "/mnt/data",
    }

    try:
        result = await lxd.patch(
            f"instances/{instance}",
            json_body={"devices": devices},
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.post(
    "/pools/{pool}/volumes/custom/{name}/detach",
    status_code=202,
    summary="Detach a custom volume from an instance (async)",
)
async def detach_volume(
    pool: str,
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    instance: str = Query(..., description="Instance name to detach from."),
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Detach a custom volume from an instance.

    Patches the instance's devices to remove the disk device for this volume.
    Maps to LXD ``PATCH /1.0/instances/{instance}``.
    """
    try:
        inst = await lxd.get(
            f"instances/{instance}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc

    devices: dict = {}
    if isinstance(inst, dict) and "devices" in inst:
        devices = dict(inst["devices"])

    device_key = f"vol_{name}"
    devices.pop(device_key, None)

    try:
        result = await lxd.patch(
            f"instances/{instance}",
            json_body={"devices": devices},
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


__all__ = ["router"]
