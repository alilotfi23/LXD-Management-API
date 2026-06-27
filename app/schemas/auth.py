"""Pydantic v2 request/response schemas for the auth endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import Role


class UserCreate(BaseModel):
    """Registration payload."""

    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(..., min_length=8, max_length=128)
    # Optional on register; defaults to "viewer" for least privilege.
    role: Role = Role.VIEWER

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role(cls, v: object) -> object:
        # Accept plain strings ("admin") coming from JSON.
        if isinstance(v, str) and v in Role._value2member_map_:
            return Role(v)
        return v


class UserOut(BaseModel):
    """Public representation of a user (never exposes the password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Role
    is_active: bool
    created_at: datetime


class LoginRequest(BaseModel):
    """OAuth2-style password login body."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """Access + refresh token pair returned by login/refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds")


class RefreshRequest(BaseModel):
    """Body for the token-refresh endpoint."""

    refresh_token: str


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
