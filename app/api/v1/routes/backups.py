"""Instance backup routes.

Maps to LXD REST endpoints (under ``/1.0``):

  GET    /instances/{name}/backups                       -> list
  POST   /instances/{name}/backups                       -> create (async op)
  GET    /instances/{name}/backups/{backup}              -> get
  DELETE /instances/{name}/backups/{backup}              -> delete (async op)
  GET    /instances/{name}/backups/{backup}/export       -> download the tarball

The export endpoint is a two-step dance in LXD: creating a backup is an async
operation (LXD builds the tarball in the background), then
``GET .../export`` streams the resulting tarball. We proxy that stream straight
to the caller as a downloadable file.

RBAC: list/get -> viewer+; create/delete -> operator+; export download -> operator+.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.deps import (
    CurrentUser,
    LXDClientDep,
    ProjectParam,
    RequireOperator,
    RequireViewer,
)
from app.schemas.snapshots import BackupCreate
from app.services.exceptions import LXDError
from app.services.lxd_operations import to_async_ref, wait_for_operation
from app.utils.pagination import PageParams, page_params, paginate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/instances/{name}/backups", tags=["backups"])


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


@router.get("", summary="List backups of an instance")
async def list_backups(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
    page: PageParams = Depends(page_params),
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/instances/{name}/backups?recursion=1``."""
    try:
        result = await lxd.get(
            f"instances/{name}/backups",
            params={"recursion": 1, "project": project},
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    items = result if isinstance(result, list) else []
    return paginate(items, page)


@router.post("", status_code=202, summary="Create a backup (async)")
async def create_backup(
    name: str,
    payload: BackupCreate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
    wait: bool = Query(False, description="Block until the backup tarball is built."),
) -> dict[str, Any]:
    """Maps to LXD ``POST /1.0/instances/{name}/backups`` (async op).

    Building a backup tarball takes time, so LXD returns an operation. Pass
    ``?wait=true`` to block until it's ready, after which ``GET .../export``
    will stream the file.
    """
    body = payload.model_dump(exclude_none=True)
    try:
        result = await lxd.post(
            f"instances/{name}/backups",
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


@router.get("/{backup}", summary="Get a backup")
async def get_backup(
    name: str,
    backup: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/instances/{name}/backups/{backup}``."""
    try:
        return await lxd.get(
            f"instances/{name}/backups/{backup}",
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.delete("/{backup}", status_code=202, summary="Delete a backup (async)")
async def delete_backup(
    name: str,
    backup: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``DELETE /1.0/instances/{name}/backups/{backup}``."""
    try:
        result = await lxd.delete(
            f"instances/{name}/backups/{backup}",
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.get(
    "/{backup}/export",
    summary="Download a backup as a tarball",
    response_class=StreamingResponse,
)
async def export_backup(
    name: str,
    backup: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
) -> StreamingResponse:
    """Maps to LXD ``GET /1.0/instances/{name}/backups/{backup}/export``.

    Streams the backup tarball to the client with a ``Content-Disposition``
    header so browsers offer it as a download. The body is binary, so we use
    ``raw_response=True`` to bypass the JSON envelope parser.
    """
    try:
        response = await lxd.request(
            "GET",
            f"instances/{name}/backups/{backup}/export",
            params={"project": project} or None,
            raw_response=True,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc

    filename = f"{name}-{backup}.tar.gz"
    return StreamingResponse(
        response.aiter_bytes(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["router"]
