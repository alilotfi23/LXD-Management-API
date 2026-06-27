"""Pydantic v2 schemas for storage pools and volumes."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---- Storage Pools ---------------------------------------------------------

class StoragePoolCreate(BaseModel):
    """Body for `POST /storage-pools` -> LXD pool create.

    The `driver` field must match a driver supported by the LXD host (e.g. zfs,
    btrfs, dir, lvm, ceph). The `config` dict passes driver-specific keys.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Pool name.",
    )
    driver: str = Field(
        ...,
        description="Storage driver: 'dir', 'zfs', 'btrfs', 'lvm', 'ceph', 'cephfs'.",
    )
    description: str = Field("", description="Human-readable description.")
    config: dict[str, str] = Field(
        default_factory=dict,
        description="Driver-specific config (source, size, zfs.pool_name, ...).",
    )


class StoragePoolUpdate(BaseModel):
    """Body for `PUT /storage-pools/{name}` (full replace)."""

    model_config = ConfigDict(extra="allow")

    description: Optional[str] = None
    config: Optional[dict[str, str]] = None


class StoragePoolPatch(BaseModel):
    """Body for `PATCH /storage-pools/{name}` (partial update)."""

    model_config = ConfigDict(extra="allow")

    description: Optional[str] = None
    config: Optional[dict[str, str]] = None


# ---- Storage Volumes -------------------------------------------------------

class StorageVolumeCreate(BaseModel):
    """Body for `POST /storage-pools/{pool}/volumes` -> LXD volume create."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    volume_type: str = Field(
        "custom",
        alias="type",
        description="Volume type: 'custom', 'container', 'image', 'vm'.",
    )
    content_type: Optional[str] = Field(
        "filesystem",
        alias="content_type",
        description="'filesystem' or 'block'.",
    )
    description: str = Field("")
    config: dict[str, str] = Field(default_factory=dict)
    source: Optional[dict[str, Any]] = Field(
        None, description="Source for cloning/copying a volume."
    )


class StorageVolumeUpdate(BaseModel):
    """Body for `PUT /storage-pools/{pool}/volumes/{type}/{name}`."""

    model_config = ConfigDict(extra="allow")

    description: Optional[str] = None
    config: Optional[dict[str, str]] = None


# ---- Volume attach / detach ------------------------------------------------

class VolumeAttachDetach(BaseModel):
    """Body for attaching/detaching a custom volume to/from an instance.

    LXD implements volume attach as an instance PATCH that adds a disk device
    whose `pool` + `volume` keys reference the custom volume.
    """

    model_config = ConfigDict(extra="allow")

    path: str = Field("/mnt/data", description="Mount point inside the instance.")
    device_name: str = Field(
        "", description="Device key on the instance; defaults to vol_<name>."
    )
