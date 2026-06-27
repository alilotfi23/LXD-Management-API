"""Instance routes (CRUD + lifecycle + exec + console).

Maps to these LXD REST endpoints (all under ``/1.0``):

  GET    /instances                      -> list   (recursion/filter/project)
  POST   /instances                      -> create (async op)
  GET    /instances/{name}               -> get
  PUT    /instances/{name}               -> full replace (async op)
  PATCH  /instances/{name}               -> partial update (async op)
  DELETE /instances/{name}               -> delete (async op)
  GET    /instances/{name}/state         -> CPU/mem/net usage + IPs
  PUT    /instances/{name}/state         -> start/stop/restart/freeze/unfreeze (async op)
  POST   /instances/{name}/exec          -> trigger exec operation (async op)
  WS     /instances/{name}/exec/ws       -> proxy exec WebSocket (interactive)
  WS     /instances/{name}/console/ws    -> proxy console WebSocket
  GET    /instances/{name}/logs          -> list log files
  GET    /instances/{name}/logs/{file}   -> stream a log file

RBAC:
  - GET/list/state/logs    -> viewer+ (read-only)
  - lifecycle/exec/console -> operator+
  - create/update/delete   -> admin
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse

from app.api.deps import (
    CurrentUser,
    LXDClientDep,
    ProjectParam,
    RequireAdmin,
    RequireOperator,
    RequireViewer,
    DbSession,
    ws_user_from_token,
)
from app.core.config import settings
from app.schemas.instances import (
    ExecRequest,
    InstanceCreate,
    InstancePatch,
    InstanceStateAction,
    InstanceUpdate,
)
from app.services.exceptions import (
    LXDConnectionError,
    LXDNotFoundError,
    LXDOperationError,
    LXDError,
)
from app.services.lxd_client import LXDClient
from app.services.lxd_operations import to_async_ref, wait_for_operation
from app.utils.pagination import PageParams, page_params, paginate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/instances", tags=["instances"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _lxd_err_to_http(exc: LXDError) -> HTTPException:
    """Map LXDError subclasses onto the matching HTTP status."""
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
# CRUD
# ---------------------------------------------------------------------------
@router.get("", summary="List instances")
async def list_instances(
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    instance_type: Annotated[
        Optional[str],
        Query(
            alias="instance-type",
            description="Filter by type: 'container' or 'virtual-machine'.",
        ),
    ] = None,
    project: ProjectParam = None,
    page: PageParams = Depends(page_params),
    expand: bool = Query(True),
    filter: Optional[str] = Query(None),
) -> dict[str, Any]:
    """List instances.

    Maps to LXD ``GET /1.0/instances?recursion=1&project=...``. Supports the
    ``instance-type`` filter, LXD's OData ``filter``, ``?expand=false`` (URLs
    only), and our own ``limit``/``offset`` pagination on top.
    """
    extra: dict[str, Any] = {}
    if instance_type:
        extra["type"] = instance_type
    try:
        result = await lxd.get(
            "instances",
            params={
                "recursion": 1 if expand else 0,
                "filter": filter,
                "project": project,
                **extra,
            },
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc

    items = result if isinstance(result, list) else []
    return paginate(items, page)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an instance (async)",
)
async def create_instance(
    payload: InstanceCreate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Create an instance.

    Maps to LXD ``POST /1.0/instances``. Because instance creation (downloading
    the image, unpacking the rootfs) is long-running, LXD returns 202 + an
    operation id. We return that operation reference immediately so the client
    can poll ``GET /api/v1/operations/{id}`` or subscribe to the operations WS.
    """
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.post(
            "instances", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    ref = to_async_ref(result)
    return ref.model_dump()


@router.get("/{name}", summary="Get an instance")
async def get_instance(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/instances/{name}?project=...``."""
    try:
        return await lxd.get(
            f"instances/{name}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.put(
    "/{name}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Full-replace instance config (async)",
)
async def replace_instance(
    name: str,
    payload: InstanceUpdate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``PUT /1.0/instances/{name}`` (full replace, async op)."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.put(
            f"instances/{name}", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.patch(
    "/{name}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Partially update instance config (async)",
)
async def patch_instance(
    name: str,
    payload: InstancePatch,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``PATCH /1.0/instances/{name}`` (partial update, async op)."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.patch(
            f"instances/{name}", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.delete(
    "/{name}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete an instance (async)",
)
async def delete_instance(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
    force: bool = Query(False, description="Force-remove even if running."),
) -> dict[str, Any]:
    """Maps to LXD ``DELETE /1.0/instances/{name}?project=...`` (async op)."""
    try:
        result = await lxd.delete(
            f"instances/{name}",
            params={"project": project, "force": force} if (project or force) else None,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


# ---------------------------------------------------------------------------
# State / lifecycle
# ---------------------------------------------------------------------------
@router.get("/{name}/state", summary="Get instance state (CPU/mem/net/IPs)")
async def get_instance_state(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/instances/{name}/state``.

    Returns CPU/memory/network usage and IP addresses for running instances.
    """
    try:
        return await lxd.get(
            f"instances/{name}/state", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.put(
    "/{name}/state",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start/stop/restart/freeze/unfreeze an instance (async)",
)
async def set_instance_state(
    name: str,
    payload: InstanceStateAction,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
    wait: bool = Query(False, description="Block until the action completes."),
) -> dict[str, Any]:
    """Maps to LXD ``PUT /1.0/instances/{name}/state`` (async op).

    Set ``?wait=true`` to long-poll until the lifecycle action completes
    (translates to ``GET .../operations/{id}/wait``). Otherwise the operation
    reference is returned immediately.
    """
    body = payload.model_dump(exclude_none=True)
    try:
        result = await lxd.put(
            f"instances/{name}/state", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    ref = to_async_ref(result)
    if wait and ref.operation_id:
        try:
            await wait_for_operation(lxd, ref.operation_id, project=project)
        except LXDOperationError as exc:
            raise HTTPException(status_code=500, detail=exc.message) from exc
        return {"operation_id": ref.operation_id, "status": "Success"}
    return ref.model_dump()


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
@router.get("/{name}/logs", summary="List instance log files")
async def list_instance_logs(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/instances/{name}/logs`` (returns log filenames)."""
    try:
        return await lxd.get(
            f"instances/{name}/logs", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.get(
    "/{name}/logs/{filename}",
    summary="Stream an instance log file",
    response_class=StreamingResponse,
)
async def get_instance_log(
    name: str,
    filename: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> StreamingResponse:
    """Maps to LXD ``GET /1.0/instances/{name}/logs/{filename}``.

    Streams the raw log bytes back to the client.
    """
    try:
        response = await lxd.request(
            "GET",
            f"instances/{name}/logs/{filename}",
            params={"project": project} or None,
            raw_response=True,
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc

    return StreamingResponse(
        response.aiter_bytes(),
        media_type="text/plain",
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Exec (HTTP trigger + WebSocket proxy)
# ---------------------------------------------------------------------------
@router.post(
    "/{name}/exec",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger an exec operation (non-interactive)",
)
async def exec_instance(
    name: str,
    payload: ExecRequest,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``POST /1.0/instances/{name}/exec``.

    For non-interactive (``interactive: false``) runs this returns an operation
    reference; the command's stdout/stderr are captured in the operation
    metadata. For interactive use, connect via the WebSocket endpoint below.
    """
    body = payload.model_dump(exclude_none=True)
    try:
        result = await lxd.post(
            f"instances/{name}/exec", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.websocket("/{name}/exec/ws")
async def exec_ws(
    websocket: WebSocket,
    name: str,
    db: DbSession,
    lxd: LXDClientDep,
    project: Optional[str] = None,
) -> None:
    """Bidirectional WebSocket proxy for interactive exec.

    Browser JS cannot set custom headers on a WS upgrade, so the JWT is passed
    as ``?token=<access-jwt>``. Flow:

      1. Validate token -> user; check role >= operator.
      2. Trigger LXD ``POST /1.0/instances/{name}/exec`` with ``interactive: true``
         (default shell). LXD returns an operation with secrets for the
         control + data fds.
      3. Open LXD's exec data WebSocket
         (``/1.0/operations/{id}/websocket?secret=<data-secret>``) and proxy
         bytes both ways until either side closes.

    Because we hold the LXD connection server-side, the browser only ever talks
    to us — local Unix-socket LXD becomes reachable from a remote browser too.
    """
    # Accept must happen before reading query params in some clients; read first.
    token = websocket.query_params.get("token")
    command = websocket.query_params.get("command", "/bin/sh")
    if not token:
        await websocket.close(code=4401)
        return
    try:
        user = await ws_user_from_token(token, db)
    except HTTPException:
        await websocket.close(code=4401)
        return
    from app.core.security import Role

    try:
        role = Role(user.role)
    except ValueError:
        role = Role.VIEWER
    if not role.at_least(Role.OPERATOR):
        await websocket.close(code=4403)
        return

    await websocket.accept()

    # Trigger the exec operation.
    exec_body = {
        "command": [command],
        "environment": {"TERM": "xterm-256color"},
        "interactive": True,
        "width": 80,
        "height": 24,
    }
    params = {"project": project} if project else None
    try:
        op = await lxd.post(f"instances/{name}/exec", json_body=exec_body, params=params)
    except LXDError as exc:
        await websocket.send_text(f"exec failed: {exc.message}")
        await websocket.close(code=1011)
        return

    op_dict = op if isinstance(op, dict) else {"id": str(op)}
    op_id = op_dict.get("id", "")
    metadata = op_dict.get("metadata", {}) if isinstance(op_dict.get("metadata"), dict) else {}
    # LXD exec interactive op exposes secrets for fds control/0/1/2.
    data_secret = metadata.get("metadata", {}).get("1") if isinstance(
        metadata.get("metadata"), dict
    ) else None
    if not data_secret and isinstance(op_dict.get("metadata"), dict):
        data_secret = op_dict["metadata"].get("metadata", {}).get("1") if isinstance(
            op_dict["metadata"].get("metadata"), dict
        ) else None

    await _proxy_exec_ws(websocket, lxd, op_id, data_secret)


async def _proxy_exec_ws(
    websocket: WebSocket,
    lxd: LXDClient,
    op_id: str,
    data_secret: Optional[str],
) -> None:
    """Relay bytes between the client WebSocket and LXD's exec WebSocket.

    Uses the `websockets` library against LXD's operation WS endpoint. We pull
    the LXD WS URL from the client (handles both local socket and remote TLS).
    """
    import asyncio

    try:
        from websockets.asyncio.client import connect as ws_connect
    except ImportError:  # pragma: no cover
        from websockets.client import connect as ws_connect  # type: ignore

    if not data_secret:
        await websocket.send_text("no exec data secret returned by LXD")
        await websocket.close(code=1011)
        return

    lxd_ws_url = lxd.ws_url(
        f"operations/{op_id}/websocket", secret=data_secret
    )

    # Build the connection kwargs for mTLS in remote mode.
    connect_kwargs: dict[str, Any] = {}
    if settings.is_local_mode:
        # Local socket WS: websockets can speak to a Unix socket via its path.
        connect_kwargs["unix"] = False  # explicit; we rewrite url below
        # websockets lib doesn't support http+unix directly; for local mode we
        # connect to the socket path with the path embedded.
        connect_kwargs["uri"] = lxd_ws_url
    else:
        connect_kwargs["uri"] = lxd_ws_url
        if settings.LXD_CLIENT_CERT_PATH and settings.LXD_CLIENT_KEY_PATH:
            connect_kwargs["cert"] = (
                settings.LXD_CLIENT_CERT_PATH,
                settings.LXD_CLIENT_KEY_PATH,
            )
        connect_kwargs["verify_ssl"] = bool(
            settings.LXD_TRUSTED_CA_PATH
        ) and settings.LXD_TRUSTED_CA_PATH != ""

    try:
        async with ws_connect(**connect_kwargs) as lxd_ws:
            async def client_to_lxd() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        text = msg.get("text") or msg.get("bytes")
                        if text:
                            await lxd_ws.send(text)
                except WebSocketDisconnect:
                    pass

            async def lxd_to_client() -> None:
                try:
                    async for msg in lxd_ws:
                        if isinstance(msg, (bytes, bytearray)):
                            await websocket.send_bytes(bytes(msg))
                        else:
                            await websocket.send_text(str(msg))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("LXD exec ws read ended: %s", exc)

            await asyncio.gather(client_to_lxd(), lxd_to_client())
    except Exception as exc:  # noqa: BLE001
        logger.exception("exec websocket proxy failed")
        try:
            await websocket.send_text(f"proxy error: {exc}")
        finally:
            await websocket.close(code=1011)


# ---------------------------------------------------------------------------
# Console WebSocket
# ---------------------------------------------------------------------------
@router.websocket("/{name}/console/ws")
async def console_ws(
    websocket: WebSocket,
    name: str,
    db: DbSession,
    lxd: LXDClientDep,
    type: str = "console",
    project: Optional[str] = None,
) -> None:
    """Proxy LXD's instance console WebSocket.

    Authenticates via ``?token=`` (same as exec). Triggers
    ``POST /1.0/instances/{name}/console`` which returns an operation with a
    single secret, then proxies bytes between the client and LXD's console WS.
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
    from app.core.security import Role

    try:
        role = Role(user.role)
    except ValueError:
        role = Role.VIEWER
    if not role.at_least(Role.OPERATOR):
        await websocket.close(code=4403)
        return

    await websocket.accept()

    params = {"project": project} if project else None
    try:
        op = await lxd.post(
            f"instances/{name}/console",
            json_body={"type": type, "width": 80, "height": 24},
            params=params,
        )
    except LXDError as exc:
        await websocket.send_text(f"console attach failed: {exc.message}")
        await websocket.close(code=1011)
        return

    op_dict = op if isinstance(op, dict) else {"id": str(op)}
    op_id = op_dict.get("id", "")
    secret = None
    md = op_dict.get("metadata", {})
    if isinstance(md, dict):
        inner = md.get("metadata", {})
        if isinstance(inner, dict):
            secret = inner.get("0") or inner.get("console")

    # Reuse the same proxy plumbing as exec (single-secret console).
    await _proxy_exec_ws(websocket, lxd, op_id, secret)


__all__ = ["router"]
