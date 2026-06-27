"""Pydantic v2 schemas for images."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ImageCreate(BaseModel):
    """Body for `POST /images` — import an image from file upload or remote."""

    model_config = ConfigDict(extra="allow")

    # For remote copy: source server + alias/fingerprint
    source: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Source spec for copying from a remote image server, e.g. "
            "{'type': 'image', 'alias': 'ubuntu/22.04', "
            "'server': 'https://images.linuxcontainers.org', "
            "'protocol': 'simplestreams'}."
        ),
    )
    # For file import
    filename: Optional[str] = Field(None, description="Filename for file-based import.")
    # Common properties
    public: bool = Field(False, description="Make the image publicly available.")
    auto_update: bool = Field(False, description="Auto-update from the source.")
    properties: dict[str, str] = Field(default_factory=dict)


class ImageAliasCreate(BaseModel):
    """Body for creating an alias for an image."""

    name: str = Field(..., min_length=1)
    description: str = Field("")
    target: str = Field(..., description="Image fingerprint the alias points to.")
