"""Pydantic v2 schemas for networks."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class NetworkCreate(BaseModel):
    """Body for `POST /networks` -> LXD managed network create."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    description: str = Field("")
    # "bridge", "macvlan", "sriov", "ovn", "physical"
    type: str = Field("bridge", description="Network type (bridge is default for managed).")
    config: dict[str, str] = Field(
        default_factory=dict,
        description="Network config (ipv4.address, ipv6.address, ...).",
    )


class NetworkUpdate(BaseModel):
    """Body for `PUT /networks/{name}` (full replace)."""

    model_config = ConfigDict(extra="allow")

    description: Optional[str] = None
    config: Optional[dict[str, str]] = None


class NetworkPatch(BaseModel):
    """Body for `PATCH /networks/{name}` (partial update)."""

    model_config = ConfigDict(extra="allow")

    description: Optional[str] = None
    config: Optional[dict[str, str]] = None


class NicAttach(BaseModel):
    """Body for attaching a NIC to an instance.

    LXD uses a `nic` device entry on the instance. We expose the common fields.
    """

    model_config = ConfigDict(extra="allow")

    network: str = Field(..., description="Network name to connect to.")
    name: str = Field("eth0", description="Device name inside the instance.")
    device_name: str = Field("", description="Device key; defaults to nic_<network>.")
