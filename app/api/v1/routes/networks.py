"""Network routes + NIC attach/detach convenience endpoints.

Maps to LXD REST endpoints (under ``/1.0``):

  **Networks:**
    GET    /networks                       -> list networks
    POST   /networks                       -> create managed network (async)
    GET    /networks/{name}                -> get network
    PUT    /networks/{name}                -> update network (async)
    PATCH  /networks/{name}                -> patch network (async)
    DELETE /networks/{name}                -> delete network (async)
    GET    /networks/{name}/state          -> network lease/state (MAC/IP bindings)

  **NIC attach/detach:**
    Like volume attach, LXD handles NICs by PATCHing the instance's device
    list with a `nic` device entry. We expose convenience endpoints.

RBAC:
  - GET            -> viewer+
  - create/update/delete -> admin (infra-level)
  - NIC attach/detach    -> operator+
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
from app.schemas.networks import NetworkCreate, NetworkPatch, NetworkUpdate, NicAttach
from app.services.exceptions import LXDError
from app.services.lxd_operations import to_async_ref
from app.utils.pagination import PageParams, page_params, paginate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/networks", tags=["networks"])


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
# Networks
# ============================================================

@router.get("", summary="List networks")
async def list_networks(
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
    page: PageParams = Depends(page_params),
    expand: bool = Query(True),
) -> dict[str, Any]:
    """Maps to LXD ``GET /1.0/networks?recursion=1``."""
    try:
        result = await lxd.get(
            "networks",
            params={"recursion": 1 if expand else 0, "project": project},
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    items = result if isinstance(result, list) else []
    return paginate(items, page)


@router.post("", status_code=202, summary="Create a managed network (async)")
async def create_network(
    payload: NetworkCreate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``POST /1.0/networks`` (async op)."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.post(
            "networks", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.get("/{name}", summary="Get a network")
async def get_network(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/networks/{name}``."""
    try:
        return await lxd.get(
            f"networks/{name}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


@router.put("/{name}", status_code=202, summary="Update a network (async)")
async def update_network(
    name: str,
    payload: NetworkUpdate,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``PUT /1.0/networks/{name}``."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.put(
            f"networks/{name}", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.patch("/{name}", status_code=202, summary="Patch a network (async)")
async def patch_network(
    name: str,
    payload: NetworkPatch,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``PATCH /1.0/networks/{name}``."""
    body = payload.model_dump(exclude_none=True, by_alias=True)
    try:
        result = await lxd.patch(
            f"networks/{name}", json_body=body, params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.delete("/{name}", status_code=202, summary="Delete a network (async)")
async def delete_network(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireAdmin,
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Maps to LXD ``DELETE /1.0/networks/{name}``."""
    try:
        result = await lxd.delete(
            f"networks/{name}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc
    return to_async_ref(result).model_dump()


@router.get("/{name}/state", summary="Get network state (leases / addresses)")
async def get_network_state(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireViewer,
    project: ProjectParam = None,
) -> Any:
    """Maps to LXD ``GET /1.0/networks/{name}/state``.

    Returns current DHCP leases, bridge counters, etc.
    """
    try:
        return await lxd.get(
            f"networks/{name}/state", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc


# ============================================================
# NIC attach / detach (convenience over instance PATCH)
# ============================================================

@router.post(
    "/{name}/attach",
    status_code=202,
    summary="Attach a NIC to an instance (async)",
)
async def attach_nic(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    instance: str = Query(..., description="Instance name to attach NIC to."),
    device_name: Optional[str] = Query(None, description="Device key on the instance."),
    nic_name: str = Query("eth0", description="Interface name inside the instance."),
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Attach a network to an instance as a NIC device.

    Patches the instance's devices list with a `nic` device pointing at this
    network. Maps to LXD ``PATCH /1.0/instances/{instance}``.
    """
    try:
        inst = await lxd.get(
            f"instances/{instance}", params={"project": project} or None
        )
    except LXDError as exc:
        raise _lxd_err_to_http(exc) from exc

    devices = {}
    if isinstance(inst, dict) and "devices" in inst:
        devices = dict(inst["devices"])

    dev_key = device_name or f"nic_{name}"
    devices[dev_key] = {
        "type": "nic",
        "network": name,
        "name": nic_name,
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
    "/{name}/detach",
    status_code=202,
    summary="Detach a NIC from an instance (async)",
)
async def detach_nic(
    name: str,
    lxd: LXDClientDep,
    _: CurrentUser = RequireOperator,
    instance: str = Query(..., description="Instance name to detach NIC from."),
    device_name: Optional[str] = Query(None, description="Device key to remove."),
    project: ProjectParam = None,
) -> dict[str, Any]:
    """Detach a NIC device from an instance.

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

    dev_key = device_name or f"nic_{name}"
    devices.pop(dev_key, None)

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
