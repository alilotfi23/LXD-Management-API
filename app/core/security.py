"""Security helpers: password hashing and JWT token handling.

Two distinct authentication concerns live in this project:

1. **LXD's own TLS auth** — used *only* for the API-to-LXD connection in remote
   mode (client cert + key). Handled in `services/lxd_client.py`.
2. **JWT auth for clients of THIS API** — handled here. These tokens are how a
   browser/frontend authenticates to our REST endpoints; they have nothing to do
   with LXD directly.

Role hierarchy:
    admin    (role level 3) — full access including user & infra management
    operator (role level 2) — instance lifecycle + attach/detach, no infra CRUD
    viewer   (role level 1) — read-only (GET endpoints)
``require_role("operator")`` therefore also admits `admin` (higher level).
"""

from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# ---- Password hashing ------------------------------------------------------
# passlib wraps bcrypt; we pin bcrypt separately to avoid the known 4.0/4.1
# wheel incompatibility with passlib 1.7.4.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Hash a plaintext password for storage."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


# ---- Roles -----------------------------------------------------------------
class Role(str, enum.Enum):
    """User roles, ordered so a higher level implies all lower privileges."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

    @property
    def level(self) -> int:
        return {Role.VIEWER: 1, Role.OPERATOR: 2, Role.ADMIN: 3}[self]

    def at_least(self, other: "Role") -> bool:
        """True if this role has the privileges of `other` (hierarchy)."""
        return self.level >= other.level


# ---- JWT -------------------------------------------------------------------
TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, expired, or invalid."""


def _create_token(
    subject: str,
    token_type: TokenType,
    *,
    extra: dict[str, Any] | None = None,
) -> str:
    """Build and sign a JWT.

    `subject` is the username; the `type` claim distinguishes access vs refresh
    tokens so a refresh token cannot be used as an access token and vice-versa.
    """
    now = datetime.now(timezone.utc)
    if token_type == "access":
        expires_delta = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    else:
        expires_delta = timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(
    subject: str, *, role: str, extra: dict[str, Any] | None = None
) -> str:
    """Create a short-lived access token."""
    return _create_token(subject, "access", extra={"role": role, **(extra or {})})


def create_refresh_token(subject: str) -> str:
    """Create a long-lived refresh token (no role claim; re-issued on refresh)."""
    return _create_token(subject, "refresh")


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT's signature + expiry.

    Raises `TokenError` on any failure so callers can map it to a 401.
    """
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:  # pragma: no cover - defensive
        raise TokenError(str(exc)) from exc
