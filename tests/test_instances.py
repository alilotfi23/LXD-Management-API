"""Tests for the instance endpoints (list, create, delete, RBAC)."""

from __future__ import annotations

import pytest

from tests.conftest import lxd_async, lxd_sync

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
class TestInstanceListing:
    async def test_list_instances_returns_paginated_envelope(
        self, client, admin_headers, mock_lxd
    ):
        """GET /instances maps to LXD list and wraps in our pagination envelope."""
        mock_lxd.get.return_value = [
            {"name": "web1", "status": "Running", "type": "container"},
            {"name": "db1", "status": "Stopped", "type": "container"},
        ]

        resp = await client.get("/api/v1/instances", headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["limit"] == 100
        assert body["offset"] == 0
        names = [i["name"] for i in body["items"]]
        assert names == ["web1", "db1"]

        # Verify the call hit the right LXD endpoint with recursion=1.
        mock_lxd.get.assert_awaited_once()
        call = mock_lxd.get.call_args
        assert call.args[0] == "instances"
        assert call.kwargs["params"]["recursion"] == 1

    async def test_list_instances_pagination(
        self, client, admin_headers, mock_lxd
    ):
        """limit/offset slices the LXD result set."""
        mock_lxd.get.return_value = [{"name": f"c{i}"} for i in range(5)]

        resp = await client.get(
            "/api/v1/instances?limit=2&offset=1", headers=admin_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == 1
        assert [i["name"] for i in body["items"]] == ["c1", "c2"]

    async def test_list_instances_recursion_off_when_expand_false(
        self, client, admin_headers, mock_lxd
    ):
        """?expand=false maps to LXD recursion=0."""
        mock_lxd.get.return_value = ["/1.0/instances/web1"]

        resp = await client.get(
            "/api/v1/instances?expand=false", headers=admin_headers
        )
        assert resp.status_code == 200
        call = mock_lxd.get.call_args
        assert call.kwargs["params"]["recursion"] == 0

    async def test_list_requires_auth(self, client, mock_lxd):
        resp = await client.get("/api/v1/instances")
        assert resp.status_code == 401
        mock_lxd.get.assert_not_awaited()


@pytest.mark.asyncio
class TestInstanceCreate:
    async def test_create_instance_returns_async_op_ref(
        self, client, admin_headers, mock_lxd
    ):
        """POST /instances returns 202 + operation reference (not a blocking result)."""
        mock_lxd.post.return_value = lxd_async("create-op-1")

        resp = await client.post(
            "/api/v1/instances",
            json={
                "name": "web1",
                "source": {"type": "image", "alias": "ubuntu/22.04"},
                "instance_type": "container",
            },
            headers=admin_headers,
        )

        assert resp.status_code == 202
        body = resp.json()
        assert body["operation_id"] == "create-op-1"
        assert body["operation_url"] == "/1.0/operations/create-op-1"
        assert body["poll_url"] == "/api/v1/operations/create-op-1"
        assert body["wait_url"] == "/api/v1/operations/create-op-1/wait"

        # Verify the LXD POST body preserved name + source.
        mock_lxd.post.assert_awaited_once()
        sent_body = mock_lxd.post.call_args.kwargs["json_body"]
        assert sent_body["name"] == "web1"
        assert sent_body["source"]["alias"] == "ubuntu/22.04"

    async def test_create_requires_admin_not_operator(
        self, client, viewer_headers, mock_lxd
    ):
        """Instance creation is admin-only; viewer must be forbidden."""
        resp = await client.post(
            "/api/v1/instances",
            json={"name": "x", "source": {"type": "image", "alias": "ubuntu/22.04"}},
            headers=viewer_headers,
        )
        assert resp.status_code == 403
        mock_lxd.post.assert_not_awaited()

    async def test_create_invalid_name_rejected(
        self, client, admin_headers, mock_lxd
    ):
        """Names must be DNS-safe; invalid names fail pydantic validation."""
        resp = await client.post(
            "/api/v1/instances",
            json={"name": "bad name!", "source": {"type": "image", "alias": "x"}},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        mock_lxd.post.assert_not_awaited()


@pytest.mark.asyncio
class TestInstanceDelete:
    async def test_delete_instance_returns_async_op_ref(
        self, client, admin_headers, mock_lxd
    ):
        mock_lxd.delete.return_value = lxd_async("delete-op-1")

        resp = await client.delete("/api/v1/instances/web1", headers=admin_headers)
        assert resp.status_code == 202
        assert resp.json()["operation_id"] == "delete-op-1"

        mock_lxd.delete.assert_awaited_once()
        assert mock_lxd.delete.call_args.args[0] == "instances/web1"

    async def test_delete_not_found(self, client, admin_headers, mock_lxd):
        """LXD 404 maps to HTTP 404."""
        from app.services.exceptions import LXDNotFoundError

        mock_lxd.delete.side_effect = LXDNotFoundError("not found", 404)
        resp = await client.delete("/api/v1/instances/missing", headers=admin_headers)
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestInstanceState:
    async def test_start_instance_maps_to_put_state(
        self, client, admin_headers, mock_lxd
    ):
        """PUT /instances/{name}/state with action=start -> LXD PUT (async op)."""
        mock_lxd.put.return_value = lxd_async("start-op-1")

        resp = await client.put(
            "/api/v1/instances/web1/state",
            json={"action": "start"},
            headers=admin_headers,
        )
        assert resp.status_code == 202
        assert resp.json()["operation_id"] == "start-op-1"

        mock_lxd.put.assert_awaited_once()
        assert mock_lxd.put.call_args.args[0] == "instances/web1/state"
        assert mock_lxd.put.call_args.kwargs["json_body"]["action"] == "start"

    async def test_get_state_returns_instance_metrics(
        self, client, admin_headers, mock_lxd
    ):
        mock_lxd.get.return_value = lxd_sync(
            {"status": "Running", "cpu": {"usage": 12345}, "memory": {"usage": 67890}}
        )
        resp = await client.get("/api/v1/instances/web1/state", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "Running"
