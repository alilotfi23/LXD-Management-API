"""Pydantic models for LXD REST API response envelopes.

LXD uses exactly three response shapes (see the LXD REST API docs):

1. **sync** — `{"type": "sync", "status": "Success", "status_code": 200,
   "metadata": {...}}` — the request is fully done, `metadata` holds the data.

2. **async** — `{"type": "async", "status": "Operation created",
   "status_code": 202, "operation": "/1.0/operations/<uuid>", "metadata":
   {...operation...}}` — LXD accepted the work and is running it in the
   background. The client must then poll `/wait` or subscribe to the operation
   WebSocket to learn the outcome. *This is why create-instance, image-copy,
   etc. can't just return a synchronous result.*

3. **error** — `{"type": "error", "error": "...", "error_code": 404}` — the
   request failed immediately.

These models let us parse each shape safely and also model an operation's
running state. The `metadata` field is left as `Any` (or `dict`) because LXD's
metadata shape varies per resource; callers shape it via per-resource schemas
in the route modules.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# LXD response type discriminator.
LXDResponseType = Literal["sync", "async", "error"]

# LXD operation status strings (subset relevant to us).
OperationStatus = Literal[
    "Running", "Success", "Failure", "Cancelled", "Pending"
]


class LXDResponse(BaseModel):
    """Generic LXD envelope. We inspect `type` then read the relevant fields."""

    model_config = ConfigDict(extra="allow")

    type: LXDResponseType
    status: Optional[str] = None
    status_code: Optional[int] = None
    # Present on async responses: "/1.0/operations/<uuid>"
    operation: Optional[str] = None
    # Present on sync responses: the actual payload.
    metadata: Any = None
    # Present on error responses:
    error: Optional[str] = None
    error_code: Optional[int] = None

    @property
    def is_sync(self) -> bool:
        return self.type == "sync"

    @property
    def is_async(self) -> bool:
        return self.type == "async"

    @property
    def is_error(self) -> bool:
        return self.type == "error"


class LXDOperationMetadata(BaseModel):
    """The `metadata` object of an LXD operation (async envelope)."""

    model_config = ConfigDict(extra="allow")

    id: str
    class_field: str = Field(default="", alias="class")
    created_at: str = ""
    updated_at: str = ""
    status: OperationStatus = "Pending"
    status_code: int = 0
    resources: dict[str, Any] = Field(default_factory=dict)
    metadata: Any = None
    may_cancel: bool = False
    err: str = ""
    location: str = ""


class AsyncOperationRef(BaseModel):
    """Compact reference to a background operation, returned to API clients.

    Instead of blocking the HTTP request until the (possibly minutes-long)
    operation finishes, we return this reference immediately so the client can
    poll `GET /operations/{id}`, long-poll `GET /operations/{id}/wait`, or
    subscribe to `WS /operations/ws`.
    """

    operation_id: str
    operation_url: str = Field(..., description="LXD operation path, e.g. /1.0/operations/<uuid>")
    status: str = "async"
    # Where to poll/wait from our own API.
    poll_url: str = Field("", description="Our /api/v1/operations/{id} endpoint")
    wait_url: str = Field("", description="Our /api/v1/operations/{id}/wait endpoint")
