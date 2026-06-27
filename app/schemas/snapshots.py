"""Pydantic v2 schemas for instance snapshots and backups."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SnapshotCreate(BaseModel):
    """Body for `POST /instances/{name}/snapshots` -> LXD snapshot create."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1, max_length=63, pattern=r"^[A-Za-z0-9-_]+$")
    stateful: bool = Field(
        False,
        description="If true and instance is a running container, capture runtime state.",
    )
    expires_at: Optional[str] = Field(
        None, description="RFC3339 expiry; absent = no auto-expiry."
    )


class SnapshotRestore(BaseModel):
    """Body for `PUT /instances/{name}` to restore a snapshot."""

    model_config = ConfigDict(extra="allow")

    restore: str = Field(..., description="Snapshot name to restore from.")


class BackupCreate(BaseModel):
    """Body for `POST /instances/{name}/backups` -> LXD backup create."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1, max_length=63, pattern=r"^[A-Za-z0-9-_]+$")
    expires_at: Optional[str] = Field(None, description="RFC3339 expiry timestamp.")
    instance_only: bool = Field(
        False, description="Exclude snapshots from the backup."
    )
    container_only: bool = Field(  # legacy alias kept for compatibility
        False, description="Alias of instance_only (older LXD)."
    )
    optimized_storage: bool = Field(
        False, description="Use driver-specific format for smaller/faster backups."
    )
    compression_algorithm: Optional[str] = Field(
        None, description="e.g. 'gzip', 'bzip2', 'xz', 'none'."
    )


class BackupTarget(BaseModel):
    """Optional export/export config."""

    model_config = ConfigDict(extra="allow")

    target: Literal["download", "file"] = Field(
        "download", description="How the backup is delivered."
    )
