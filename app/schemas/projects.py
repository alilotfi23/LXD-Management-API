"""Pydantic v2 schemas for LXD projects."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    """Body for `POST /projects` -> LXD project create."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    description: str = Field("")
    config: dict[str, str] = Field(
        default_factory=dict,
        description="Project config keys (e.g. features.images, limits.instances).",
    )


class ProjectUpdate(BaseModel):
    """Body for `PUT /projects/{name}` (full replace)."""

    model_config = ConfigDict(extra="allow")

    description: Optional[str] = None
    config: Optional[dict[str, str]] = None


class ProjectPatch(BaseModel):
    """Body for `PATCH /projects/{name}` (partial update)."""

    model_config = ConfigDict(extra="allow")

    description: Optional[str] = None
    config: Optional[dict[str, str]] = None
