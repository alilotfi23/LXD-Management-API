"""Async operation helpers for the LXD REST API.

Many LXD actions (create instance, copy image, snapshot, migrate) are *async*:
LXD returns HTTP 202 + an ``operation`` URL immediately and runs the work in the
background. Three things happen here:

1. ``operation_id_from_url`` — turn ``/1.0/operations/<uuid>`` into the bare id.
2. ``to_async_ref`` — build the compact ``AsyncOperationRef`` we hand back to
   API clients, including *our* poll/wait URLs so they don't need to know LXD's
   URL scheme.
3. ``wait_for_operation`` — long-poll LXD's ``/wait`` endpoint and raise
   ``LXDOperationError`` if the operation ended in Failure/Cancelled. Useful
   for the few cases where a route wants to block until completion.

The WebSocket relay for live operation events lives in the operations route
(step 11); this module holds the pure logic shared across routes.
"""

from __future__ import annotations

import logging
from typing import Any

from app.schemas.lxd import AsyncOperationRef
from app.services.exceptions import LXDOperationError
from app.services.lxd_client import LXDClient

logger = logging.getLogger(__name__)


def operation_id_from_url(url: str | None) -> str | None:
    """Extract the bare operation id from an LXD operation URL.

    ``/1.0/operations/<uuid>`` -> ``<uuid>``. Accepts a bare id too.
    """
    if not url:
        return None
    return url.rstrip("/").split("/")[-1]


def to_async_ref(metadata: Any) -> AsyncOperationRef:
    """Build an ``AsyncOperationRef`` from an LXD async operation's metadata.

    `metadata` is either the operation dict (``{"id": ..., "status": ...}``)
    or ``None`` (in which case the caller should pass the operation URL via
    a separate path; we handle the dict form which is what LXD returns).
    """
    if isinstance(metadata, dict):
        op_id = metadata.get("id", "")
        op_url = metadata.get("operation") or f"/1.0/operations/{op_id}"
    else:
        op_id = str(metadata)
        op_url = f"/1.0/operations/{op_id}"

    # If the dict carried an explicit operation URL prefer it.
    if isinstance(metadata, dict) and metadata.get("operation"):
        op_url = metadata["operation"]

    return AsyncOperationRef(
        operation_id=op_id,
        operation_url=op_url,
        poll_url=f"/api/v1/operations/{op_id}",
        wait_url=f"/api/v1/operations/{op_id}/wait",
    )


async def wait_for_operation(
    client: LXDClient,
    operation_id: str,
    *,
    timeout: float = 0.0,
    project: str | None = None,
) -> dict[str, Any]:
    """Long-poll LXD's ``GET /1.0/operations/{id}/wait``.

    LXD blocks the request until the operation reaches a terminal state
    (Success/Failure/Cancelled) or `timeout` elapses. We then raise
    ``LXDOperationError`` on a non-success terminal state, else return the
    operation metadata.

    Set ``timeout=0`` (default) for an unbounded wait; pass a positive number
    to bound it. The `?timeout=` we send to LXD is in *seconds*.
    """
    params: dict[str, Any] = {}
    if timeout and timeout > 0:
        params["timeout"] = timeout
    if project:
        params["project"] = project

    result = await client.get(
        f"operations/{operation_id}/wait", params=params or None
    )
    if isinstance(result, dict):
        status = result.get("status", "")
        if status in ("Failure", "Cancelled"):
            err = result.get("err") or f"operation {operation_id} {status.lower()}"
            raise LXDOperationError(
                f"LXD operation {operation_id} ended in {status}: {err}"
            )
    return result if isinstance(result, dict) else {}


__all__ = [
    "operation_id_from_url",
    "to_async_ref",
    "wait_for_operation",
]
