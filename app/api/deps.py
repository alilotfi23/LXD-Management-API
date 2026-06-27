"""Shared FastAPI dependencies.

Includes:

* `get_db` — re-exported from db.session for convenience.
* `get_current_user` — resolves the JWT bearer token to a `User`.
* `require_role(...)` — RBAC factory enforcing the role hierarchy
  (admin > operator > viewer). `require_role("operator")` also admits admin.
* `get_lxd_client` — re-exported dependency for the LXD client singleton.
* `project_param` — optional `?project=` for per-request project scoping.

Token on WebSocket handshakes is read from a `?token=` query param instead of
the `Authorization` header (browsers cannot set custom headers on a WS
upgrade). See `ws_user_from_token`.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Role, TokenError, decode_token
from app.db import crud
from app.db.models import User
from app.db.session import get_db
from app.services.lxd_client import LXDClient, get_lxd_client

# Scheme that reads `Authorization: Bearer <jwt>` from the header.
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Resolve the bearer token in the `Authorization` header to a User.

    Raises 401 if the token is missing/invalid or the user is disabled.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if creds is None or not creds.credentials:
        raise unauthorized

    token = creds.credentials
    try:
        payload = decode_token(token)
    except TokenError:
        raise unauthorized

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token required",
        )

    username = payload.get("sub")
    if not username:
        raise unauthorized

    user = await crud.get_user_by_username(db, str(username))
    if user is None or not user.is_active:
        raise unauthorized
    return user


# Re-usable annotated dependency aliases.
CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
LXDClientDep = Annotated[LXDClient, Depends(get_lxd_client)]


def require_role(role: Role):
    """Return a dependency that admits any user *at least* `role`.

    Role hierarchy is admin(3) > operator(2) > viewer(1), so e.g.
    `require_role(Role.OPERATOR)` admits operator AND admin. Apply per
    endpoint deliberately (GET -> viewer+, lifecycle -> operator+,
    infra CRUD -> admin).
    """

    async def _checker(user: CurrentUser) -> User:
        try:
            user_role = Role(user.role)
        except ValueError:
            user_role = Role.VIEWER
        if not user_role.at_least(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role.value}' or higher required",
            )
        return user

    return _checker


# Convenience role dependencies.
RequireViewer = Depends(require_role(Role.VIEWER))
RequireOperator = Depends(require_role(Role.OPERATOR))
RequireAdmin = Depends(require_role(Role.ADMIN))


def project_param(
    project: Annotated[
        Optional[str],
        Query(description="LXD project to scope this request to"),
    ] = None,
) -> Optional[str]:
    """Optional `?project=` for per-request LXD project scoping.

    LXD namespaces resources by project; passing this through on every relevant
    endpoint lets a caller act on a non-default project without switching it
    globally.
    """
    return project


ProjectParam = Annotated[Optional[str], Depends(project_param)]


async def ws_user_from_token(
    token: str, db: AsyncSession
) -> User:
    """Resolve a user from a JWT passed as a WebSocket `?token=` query param.

    Browsers can't set custom headers on a WS upgrade, so the frontend passes
    the access token in the URL instead: ``ws://host/.../ws?token=<jwt>``.
    """
    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token required",
        )
    user = await crud.get_user_by_username(db, str(payload.get("sub", "")))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user


__all__ = [
    "CurrentUser",
    "DbSession",
    "LXDClientDep",
    "ProjectParam",
    "RequireAdmin",
    "RequireOperator",
    "RequireViewer",
    "get_current_user",
    "get_lxd_client",
    "project_param",
    "require_role",
    "ws_user_from_token",
]
