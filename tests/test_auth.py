"""Tests for the authentication flow (register, login, refresh, me, RBAC)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
class TestAuthFlow:
    """Full auth lifecycle: register (as admin) -> login -> refresh -> me."""

    async def test_login_returns_access_and_refresh_tokens(self, client, admin_user):
        """A seeded admin can log in and receives a token pair."""
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "password123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_type"] == "bearer"
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["expires_in"] > 0

    async def test_login_wrong_password_rejected(self, client, admin_user):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]

    async def test_login_unknown_user_rejected(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "ghost", "password": "whatever"},
        )
        assert resp.status_code == 401

    async def test_me_returns_current_user(self, client, admin_headers, admin_user):
        resp = await client.get("/api/v1/auth/me", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "admin"
        assert body["role"] == "admin"

    async def test_me_without_token_is_401(self, client):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_refresh_issues_new_token_pair(self, client, admin_user):
        # Login first to get a refresh token.
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "password123"},
        )
        refresh_token = login.json()["refresh_token"]

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_with_access_token_rejected(self, client, admin_token):
        """An access token must not be usable as a refresh token."""
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": admin_token},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestRbac:
    """Role-based access control enforcement."""

    async def test_admin_can_create_user(self, client, admin_headers):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "newop",
                "password": "password123",
                "role": "operator",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["username"] == "newop"
        assert resp.json()["role"] == "operator"

    async def test_viewer_cannot_create_user(self, client, viewer_headers):
        """Viewers lack admin rights and must be forbidden from user creation."""
        resp = await client.post(
            "/api/v1/auth/register",
            json={"username": "x", "password": "password123", "role": "viewer"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_duplicate_username_conflict(self, client, admin_headers, admin_user):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"username": "admin", "password": "password123", "role": "admin"},
            headers=admin_headers,
        )
        assert resp.status_code == 409

    async def test_register_requires_admin(self, client, viewer_headers):
        """Endpoint is admin-gated even with a well-formed body."""
        resp = await client.post(
            "/api/v1/auth/register",
            json={"username": "y", "password": "password123"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_password_too_short_validation(self, client, admin_headers):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"username": "z", "password": "short", "role": "viewer"},
            headers=admin_headers,
        )
        assert resp.status_code == 422  # pydantic validation error
