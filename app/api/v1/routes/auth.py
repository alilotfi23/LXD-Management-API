"""Authentication routes: register, login, refresh, me.

Rate-limited via `slowapi` on the credential-bearing endpoints (register/login/
refresh) to blunt brute-force attempts. JWTs issued here authenticate clients of
*our* API; they are entirely separate from LXD's own mTLS auth used for the
API-to-LXD connection.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi.util import get_remote_address  # noqa: F401  (kept for reference)

from app.api.deps import CurrentUser, DbSession, RequireAdmin
from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import (
    Role,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.db import crud
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _make_token_pair(user) -> TokenResponse:
    """Build an access+refresh TokenResponse for a user."""
    access = create_access_token(user.username, role=user.role)
    refresh = create_refresh_token(user.username)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
@limiter.limit("5/minute")
async def register(
    request: Request,  # required by slowapi
    payload: UserCreate,
    db: DbSession,
    current_user: CurrentUser = Depends(RequireAdmin),
) -> UserOut:
    """Create a new user.

    **Admin-only**: arbitrary user creation (especially with elevated roles) is
    restricted. The first admin is created via `SEED_ADMIN_*` env or via a
    direct DB row; subsequent ones come through here.
    """
    try:
        user = await crud.create_user(
            db,
            username=payload.username,
            password=payload.password,
            role=payload.role.value,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return UserOut.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in and obtain access + refresh tokens",
)
@limiter.limit("10/minute")
async def login(
    request: Request,
    payload: LoginRequest,
    db: DbSession,
) -> TokenResponse:
    """Authenticate with username + password, receive a JWT pair."""
    user = await crud.get_user_by_username(db, payload.username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    return _make_token_pair(user)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new access token",
)
@limiter.limit("10/minute")
async def refresh(
    request: Request, payload: RefreshRequest, db: DbSession
) -> TokenResponse:
    """Validate a refresh token and issue a fresh access+refresh pair."""
    try:
        decoded = decode_token(payload.refresh_token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    if decoded.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )
    user = await crud.get_user_by_username(db, str(decoded.get("sub", "")))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return _make_token_pair(user)


@router.get(
    "/me",
    response_model=UserOut,
    summary="Get the current authenticated user",
)
async def me(current_user: CurrentUser) -> UserOut:
    """Return the caller's own user profile."""
    return UserOut.model_validate(current_user)


@router.get(
    "/users",
    response_model=list[UserOut],
    summary="List all users (admin only)",
)
async def list_users(
    db: DbSession, current_user: CurrentUser = Depends(RequireAdmin)
) -> list[UserOut]:
    users = await crud.list_users(db)
    return [UserOut.model_validate(u) for u in users]
