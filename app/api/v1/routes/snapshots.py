"""Instance snapshot routes.

Maps to LXD REST endpoints (under ``/1.0``):

  GET    /instances/{name}/snapshots               -> list
  POST   /instances/{name}/snapshots               -> create (async op)
  GET    /instances/{name}/snapshots/{snap}        -> get
  PUT    /instances/{name}/snapshots/{snap}        -> rename/update (async op)
  DELETE /instances/{name}/snapshots/{snap}        -> delete (async op)

Restoring a snapshot is done by PUT-updating the instance with ``restore: <name>``,
which we expose as ``POST /instances/{name}/snapshots/{snap}/restore`` for
ergonomics.

RBAC: list/get -> viewer+; create/restore/rename/delete -> operator+.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import (
    CurrentUser,
    LXDClientDep,
    ProjectParam,
    RequireOperator,
    RequireViewer,
)
from app.schemas.snapshots import SnapshotCreate, SnapshotRestore
from app.services.exceptions import LXDError
from app.services.lxd_operations import to_async_ref, wait_for_operation
from app.utils.pagination import PageParams, page_params, paginate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/instances/{name}/snapshots", tags=["snapshots"])


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


@router.get("", summary="List snapshots of an instance")
async def list_snapshots(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
    page: PageParams = Depends(page_params),
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/instances/{name}/snapshots?recursion=1``."""
    try:
        result = await lxd.get(
            f"instances/{name}/snapshots",
            params={"recursion": 1, "project": project},
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    items = result if isinstance(result, list) else []
    return paginate(items, page)


@router.post(
    "",
    status_code=202,
    summary="Create a snapshot (async)",
)
async def create_snapshot(
    name: str,
    payload: SnapshotCreate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
    wait: bool = Query(False, description="Block until the snapshot completes."),
) -> dict[str, Any]:
    """Maps to LXD ``POST /1.0/instances/{name}/snapshots`` (async op)."""
    body = payload.model_dump(exclude_none=True)
    try:
        result = await lxd.post(
            f"instances/{name}/snapshots",
            json_body=body,
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    ref = to_async_ref(result)
    if wait and ref.operation_id:
        try:
            await wait_for_operation(lxd, ref.operation_id, project=project)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"operation_id": ref.operation_id, "status": "Success"}
    return ref.model_dump()


@router.get("/{snap}", summary="Get a snapshot")
async def get_snapshot(
    name: str,
    snap: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/instances/{name}/snapshots/{snap}``."""
    try:
        return await lxd.get(
            f"instances/{name}/snapshots/{snap}",
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.put("/{snap}", status_code=202, summary="Rename/update a snapshot (async)")
async def rename_snapshot(
    name: str,
    snap: str,
    payload: dict,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``PUT /1.0/instances/{name}/snapshots/{snap}``.

    The body is typically ``{"name": "new-name"}`` to rename.
    """
    try:
        result = await lxd.put(
            f"instances/{name}/snapshots/{snap}",
            json_body=payload,
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.delete("/{snap}", status_code=202, summary="Delete a snapshot (async)")
async def delete_snapshot(
    name: str,
    snap: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``DELETE /1.0/instances/{name}/snapshots/{snap}``."""
    try:
        result = await lxd.delete(
            f"instances/{name}/snapshots/{snap}",
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.post(
    "/{snap}/restore",
    status_code=202,
    summary="Restore an instance from a snapshot (async)",
)
async def restore_snapshot(
    name: str,
    snap: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
    wait: bool = Query(False, description="Block until restore completes."),
) -> dict[str, Any]:
    """Restore a snapshot.

    LXD implements restore as ``PUT /1.0/instances/{name}`` with body
    ``{"restore": "<snap>"}``; we expose it as a friendlier POST action.
    """
    body = SnapshotRestore(restore=snap).model_dump(exclude_none=True)
    try:
        result = await lxd.put(
            f"instances/{name}",
            json_body=body,
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    ref = to_async_ref(result)
    if wait and ref.operation_id:
        try:
            await wait_for_operation(lxd, ref.operation_id, project=project)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"operation_id": ref.operation_id, "status": "Success"}
    return ref.model_dump()


__all__ = ["router"]
