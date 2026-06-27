"""Async operation routes — poll, wait, cancel, and WebSocket relay.

Many LXD actions (create instance, copy image, snapshot, etc.) are *async*:
LXD returns HTTP 202 + an operation URL immediately and runs the work in the
background. This module provides:

  GET    /operations                    -> list current operations
  GET    /operations/{id}               -> get operation status/result
  DELETE /operations/{id}               -> cancel an operation
  GET    /operations/{id}/wait         -> long-poll until completion
  WS     /operations/ws                -> live event relay from LXD's event WS

The WS relay subscribes to LXD's ``/1.0/events`` WebSocket (which publishes
every operation lifecycle event) and relays them to connected clients. This
means a frontend can open a single WS connection and learn about *all*
operations without polling.

RBAC: all endpoints require viewer+ (read the status of background work).
Cancel requires operator+.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.api.deps import (
    CurrentUser,
    DbSession,
    LXDClientDep,
    ProjectParam,
    RequireOperator,
    RequireViewer,
    ws_user_from_token,
)
from app.core.security import Role
from app.services.exceptions import LXDConnectionError, LXDError, LXDOperationError
from app.services.lxd_operations import wait_for_operation
from app.utils.pagination import PageParams, page_params, paginate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/operations", tags=["operations"])


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


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@router.get("", summary="List running operations")
async def list_operations(
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
    page: PageParams = Depends(page_params),
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/operations?recursion=1``."""
    try:
        result = await lxd.get(
            "operations",
            params={"recursion": 1, "project": project},
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    items = result if isinstance(result, list) else []
    return paginate(items, page)


@router.get("/{operation_id}", summary="Get operation status")
async def get_operation(
    operation_id: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/operations/{id}``."""
    try:
        return await lxd.get(
            f"operations/{operation_id}",
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.delete("/{operation_id}", status_code=202, summary="Cancel an operation")
async def cancel_operation(
    operation_id: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
) -> dict[str, str]:
    """Maps to LXD ``DELETE /1.0/operations/{id}``.

    Sends a cancellation request to LXD. Whether the operation actually cancels
    depends on the operation type and LXD's internal state.
    """
    try:
        await lxd.delete(
            f"operations/{operation_id}",
            params={"project": project} or None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return {"message": f"Cancellation requested for operation {operation_id}"}


@router.get("/{operation_id}/wait", summary="Long-poll until operation completes")
async def wait_operation(
    operation_id: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
    timeout: float = Query(
        0, ge=0, description="Max seconds to wait (0 = unbounded)."
    ),
) -> Any:
    """Long-poll LXD's ``GET /1.0/operations/{id}/wait``.

    Blocks until the operation reaches a terminal state (Success/Failure/
    Cancelled) or `timeout` elapses. Returns the final operation metadata.
    On Failure/Cancelled, returns 500 with the error message.
    """
    try:
        result = await wait_for_operation(
            lxd,
            operation_id,
            timeout=timeout if timeout > 0 else 0,
            project=project,
        )
    except LXDOperationError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return result


# ---------------------------------------------------------------------------
# WebSocket relay for LXD operation events
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def operations_ws(
    websocket: WebSocket,
    db: DbSession,
    lxd: LXDClientDep,
    project: Optional[str] = None,
) -> None:
    """Relay LXD operation lifecycle events to connected WebSocket clients.

    Subscribes to LXD's ``GET /1.0/events?type=operation`` WebSocket, which
    streams JSON objects for every operation state transition. Each event is
    forwarded to the connected client as a JSON text frame.

    Authentication: ``?token=<jwt>`` (browsers can't set headers on WS upgrade).
    Role: viewer+.

    This allows a frontend dashboard to show real-time progress of all
    operations (instance creation, image copy, snapshot, etc.) without
    polling.
    """
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return
    try:
        user = await ws_user_from_token(token, db)
    except HTTPException:
        await websocket.close(code=4401)
        return
    try:
        role = Role(user.role)
    except ValueError:
        role = Role.VIEWER
    if not role.at_least(Role.VIEWER):
        await websocket.close(code=4403)
        return

    await websocket.accept()

    # Build the LXD events WebSocket URL.
    # LXD's event WS is at /1.0/events?type=operation
    event_ws_url = lxd.ws_url("events") + "&type=operation"

    # Build connection kwargs for the LXD WS.
    connect_kwargs: dict[str, Any] = {"uri": event_ws_url}
    if not settings.is_local_mode:
        if settings.LXD_CLIENT_CERT_PATH and settings.LXD_CLIENT_KEY_PATH:
            connect_kwargs["cert"] = (
                settings.LXD_CLIENT_CERT_PATH,
                settings.LXD_CLIENT_KEY_PATH,
            )
        connect_kwargs["verify_ssl"] = bool(
            settings.LXD_TRUSTED_CA_PATH
        ) and settings.LXD_TRUSTED_CA_PATH != ""

    try:
        try:
            from websockets.asyncio.client import connect as ws_connect
        except ImportError:
            from websockets.client import connect as ws_connect  # type: ignore

        async with ws_connect(**connect_kwargs) as lxd_ws:
            async def lxd_to_client() -> None:
                """Forward LXD event frames to the browser client."""
                try:
                    async for msg in lxd_ws:
                        if isinstance(msg, (bytes, bytearray)):
                            await websocket.send_bytes(bytes(msg))
                        else:
                            await websocket.send_text(str(msg))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("LXD event WS read ended: %s", exc)

            async def client_to_lxd() -> None:
                """LXD's event WS is read-only; just detect disconnects."""
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                except WebSocketDisconnect:
                    pass

            await asyncio.gather(lxd_to_client(), client_to_lxd())

    except LXDConnectionError as exc:
        await websocket.send_text(json.dumps({"error": exc.message}))
        await websocket.close(code=1011)
    except Exception as exc:  # noqa: BLE001
        logger.exception("operations WebSocket relay failed")
        try:
            await websocket.send_text(json.dumps({"error": str(exc)}))
        finally:
            await websocket.close(code=1011)


# Need settings for local mode check in the WS handler.
from app.core.config import settings  # noqa: E402

__all__ = ["router"]
