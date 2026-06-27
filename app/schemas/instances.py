"""Pydantic v2 schemas for instances.

These validate the payloads we send *to* LXD and shape what we return. LXD's
own `POST /1.0/instances` body is fairly free-form; we constrain the important
fields (name, source image, type) and allow passthrough for the rest via
`config`, `devices`, `profiles`.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

InstanceType = Literal["container", "virtual-machine"]
InstanceArchitecture = Literal[
    "x86_64", "aarch64", "ppc64le", "s390x", "riscv64"
]


class InstanceSource(BaseModel):
    """Where an instance's rootfs comes from.

    The common case is an image alias (e.g. ``ubuntu:22.04``); LXD also accepts
    a fingerprint or a remote migration source. We model the alias case and
    allow a passthrough for the others.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(
        "image", description="Usually 'image'; can be 'copy'/'migration'/'none'."
    )
    alias: Optional[str] = Field(
        None, description="Image alias, e.g. 'ubuntu:22.04' or 'images:alpine/edge'."
    )
    fingerprint: Optional[str] = Field(None, description="Exact image fingerprint.")
    # For remote aliases LXD needs the server protocol/mode.
    server: Optional[str] = Field(
        None, description="Remote image server URL (for non-default simplestreams)."
    )
    protocol: Optional[str] = Field(
        None, description="'simplestreams' or 'lxd' for the remote image server."
    )


class InstanceCreate(BaseModel):
    """`POST /api/v1/instances` body -> LXD `POST /1.0/instances`."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=r"^[a-zA-Z0-9-_]+$",
        description="Instance name (DNS-safe).",
    )
    architecture: InstanceArchitecture = Field(
        "x86_64", description="CPU architecture to run."
    )
    profiles: list[str] = Field(
        default_factory=lambda: ["default"],
        description="LXD profiles to apply (default: ['default']).",
    )
    ephemeral: bool = Field(False, description="If true, instance is destroyed on stop.")
    instance_type: InstanceType = Field(
        "container", description="'container' or 'virtual-machine'."
    )
    source: InstanceSource = Field(
        ..., description="Image/source the instance is created from."
    )
    config: dict[str, str] = Field(
        default_factory=dict,
        description="Instance config overrides, e.g. {'limits.cpu': '2'}.",
    )
    devices: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Devices to attach, e.g. {'root': {...}}.",
    )

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty/whitespace")
        return v


class InstanceUpdate(BaseModel):
    """`PUT` full-replace body for an instance."""

    model_config = ConfigDict(extra="allow")

    architecture: Optional[InstanceArchitecture] = None
    profiles: Optional[list[str]] = None
    ephemeral: Optional[bool] = None
    config: Optional[dict[str, str]] = None
    devices: Optional[dict[str, dict[str, Any]]] = None
    description: Optional[str] = None


class InstancePatch(BaseModel):
    """`PATCH` partial-update body for an instance (only changed fields)."""

    model_config = ConfigDict(extra="allow")

    config: Optional[dict[str, str]] = None
    devices: Optional[dict[str, dict[str, Any]]] = None
    description: Optional[str] = None
    profiles: Optional[list[str]] = None


class InstanceStateAction(BaseModel):
    """Body for power actions (`POST /instances/{name}/state`)."""

    action: Literal[
        "start", "stop", "restart", "freeze", "unfreeze"
    ]
    force: bool = Field(False, description="Force the action (no graceful shutdown).")
    stateful: bool = Field(
        False, description="For stop: save runtime state for stateful start."
    )
    timeout: int = Field(30, ge=0, description="Seconds to wait for graceful action.")


class ExecRequest(BaseModel):
    """Body for `POST /instances/{name}/exec` (and the exec WS trigger).

    Mirrors LXD's exec operation body. When `interactive: true`, LXD returns a
    single WebSocket (fd 0/1/2 multiplexed) identified by a `secret`.
    """

    model_config = ConfigDict(extra="allow")

    command: list[str] = Field(
        ..., min_length=1, description="Command + args, e.g. ['/bin/bash']."
    )
    environment: dict[str, str] = Field(
        default_factory=lambda: {"TERM": "xterm-256color"},
        description="Extra environment variables.",
    )
    interactive: bool = Field(
        True, description="If true, allocate a single interactive PTY (WS exec)."
    )
    width: int = Field(80, ge=1, description="Initial column count (interactive).")
    height: int = Field(24, ge=1, description="Initial row count (interactive).")
    user: int = Field(0, ge=0, description="UID to run as (default root).")
    group: int = Field(0, ge=0, description="GID to run as.")
    cwd: str = Field("/", description="Working directory.")
