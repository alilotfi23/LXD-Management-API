"""Tests for health, system endpoints, and the LXD error-to-HTTP mapping."""

from __future__ import annotations

import pytest

from app.services.exceptions import (
    LXDConnectionError,
    LXDNotFoundError,
    lxd_error_for_status,
)
from tests.conftest import lxd_sync

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
class TestHealth:
    async def test_unversioned_health_is_always_ok(self, client):
        """GET /health is cheap and never touches LXD."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body

    async def test_deep_health_reports_lxd_unreachable_on_connection_error(
        self, client, mock_lxd
    ):
        """When LXD is unreachable, /system/health reports unhealthy (not 503).

        The endpoint intentionally returns 200 with status=unhealthy so
        monitoring can distinguish 'API up, LXD down' from 'API down'.
        """
        mock_lxd.get.side_effect = LXDConnectionError("socket not found", 0)
        resp = await client.get("/api/v1/system/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "unhealthy"
        assert body["lxd"] == "unreachable"

    async def test_deep_health_reports_healthy_when_lxd_reachable(
        self, client, mock_lxd
    ):
        mock_lxd.get.return_value = lxd_sync({"server_version": "5.0"})
        resp = await client.get("/api/v1/system/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["lxd"] == "reachable"


@pytest.mark.asyncio
class TestSystemInfo:
    async def test_system_info_proxies_lxd_root(self, client, admin_headers, mock_lxd):
        mock_lxd.get.return_value = lxd_sync(
            {"environment": {"server_version": "5.21"}, "api_extensions": []}
        )
        resp = await client.get("/api/v1/system/info", headers=admin_headers)
        assert resp.status_code == 200
        # The route returns LXD's metadata directly.
        call = mock_lxd.get.call_args
        assert call.args[0] == "/"

    async def test_host_resources_proxies_lxd_resources(
        self, client, admin_headers, mock_lxd
    ):
        mock_lxd.get.return_value = lxd_sync({"cpu": {"total": 8}, "memory": {"total": 16}})
        resp = await client.get("/api/v1/system/resources", headers=admin_headers)
        assert resp.status_code == 200
        call = mock_lxd.get.call_args
        assert call.args[0] == "resources"


class TestErrorMapping:
    """The LXD error -> HTTP status mapping used by every route."""

    def test_404_maps_to_not_found(self):
        exc = lxd_error_for_status("not found", 404)
        assert isinstance(exc, LXDNotFoundError)
        assert exc.status_code == 404

    def test_400_maps_to_bad_request(self):
        from app.services.exceptions import LXDBadRequest

        exc = lxd_error_for_status("bad", 400)
        assert isinstance(exc, LXDBadRequest)

    def test_403_maps_to_auth_error(self):
        from app.services.exceptions import LXDAuthError

        exc = lxd_error_for_status("forbidden", 403)
        assert isinstance(exc, LXDAuthError)

    def test_409_maps_to_conflict(self):
        from app.services.exceptions import LXDConflictError

        exc = lxd_error_for_status("exists", 409)
        assert isinstance(exc, LXDConflictError)

    def test_unknown_status_falls_back_to_generic(self):
        from app.services.exceptions import LXDRequestError

        exc = lxd_error_for_status("weird", 500)
        assert isinstance(exc, LXDRequestError)
        assert exc.status_code == 500


class TestSecurity:
    """Password hashing + role hierarchy."""

    def test_hash_and_verify_password(self):
        from app.core.security import hash_password, verify_password

        h = hash_password("s3cret-pass")
        assert h != "s3cret-pass"
        assert verify_password("s3cret-pass", h)
        assert not verify_password("wrong", h)

    def test_role_hierarchy(self):
        from app.core.security import Role

        assert Role.ADMIN.at_least(Role.OPERATOR)
        assert Role.ADMIN.at_least(Role.VIEWER)
        assert Role.ADMIN.at_least(Role.ADMIN)
        assert Role.OPERATOR.at_least(Role.VIEWER)
        assert not Role.OPERATOR.at_least(Role.ADMIN)
        assert not Role.VIEWER.at_least(Role.OPERATOR)

    def test_role_level_values(self):
        from app.core.security import Role

        assert Role.VIEWER.level == 1
        assert Role.OPERATOR.level == 2
        assert Role.ADMIN.level == 3
