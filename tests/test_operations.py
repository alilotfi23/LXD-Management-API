"""Tests for the async operation endpoints (list, get, wait, cancel)."""

from __future__ import annotations

import pytest

from app.services.exceptions import LXDOperationError
from tests.conftest import lxd_async, lxd_sync

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
class TestOperationListing:
    async def test_list_operations_returns_paginated(
        self, client, admin_headers, mock_lxd
    ):
        """GET /operations maps to LXD list and wraps in pagination."""
        mock_lxd.get.return_value = [
            {"id": "op-1", "status": "Running"},
            {"id": "op-2", "status": "Success"},
        ]

        resp = await client.get("/api/v1/operations", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["items"][0]["id"] == "op-1"

        call = mock_lxd.get.call_args
        assert call.args[0] == "operations"
        assert call.kwargs["params"]["recursion"] == 1

    async def test_list_operations_requires_auth(self, client, mock_lxd):
        resp = await client.get("/api/v1/operations")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestOperationGet:
    async def test_get_operation_returns_metadata(
        self, client, admin_headers, mock_lxd
    ):
        mock_lxd.get.return_value = lxd_sync(
            {"id": "op-123", "status": "Running", "status_code": 103}
        )

        resp = await client.get("/api/v1/operations/op-123", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "op-123"
        assert body["status"] == "Running"

        assert mock_lxd.get.call_args.args[0] == "operations/op-123"


@pytest.mark.asyncio
class TestOperationCancel:
    async def test_cancel_operation_maps_to_delete(
        self, client, admin_headers, mock_lxd
    ):
        """DELETE /operations/{id} forwards to LXD DELETE."""
        mock_lxd.delete.return_value = lxd_sync({})

        resp = await client.delete("/api/v1/operations/op-1", headers=admin_headers)
        assert resp.status_code == 202
        assert "Cancellation requested" in resp.json()["message"]
        mock_lxd.delete.assert_awaited_once()

    async def test_cancel_requires_operator_not_viewer(
        self, client, viewer_headers, mock_lxd
    ):
        """Cancellation is operator+; viewer must be forbidden."""
        resp = await client.delete("/api/v1/operations/op-1", headers=viewer_headers)
        assert resp.status_code == 403
        mock_lxd.delete.assert_not_awaited()


@pytest.mark.asyncio
class TestOperationWait:
    async def test_wait_returns_terminal_metadata_on_success(
        self, client, admin_headers, mock_lxd
    ):
        """GET /operations/{id}/wait long-polls and returns the final state."""
        mock_lxd.get.return_value = lxd_sync(
            {"id": "op-1", "status": "Success", "status_code": 200}
        )

        resp = await client.get("/api/v1/operations/op-1/wait", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "Success"

        # Should have hit the LXD /wait endpoint with the operation id.
        call = mock_lxd.get.call_args
        assert call.args[0] == "operations/op-1/wait"

    async def test_wait_on_failure_returns_500(
        self, client, admin_headers, mock_lxd
    ):
        """A failed operation surfaces as a 500 with the LXD error text."""
        mock_lxd.get.side_effect = LXDOperationError(
            "operation op-bad ended in failure: disk full", 0
        )

        resp = await client.get(
            "/api/v1/operations/op-bad/wait", headers=admin_headers
        )
        assert resp.status_code == 500
        assert "failure" in resp.json()["detail"].lower()

    async def test_wait_passes_timeout_to_lxd(
        self, client, admin_headers, mock_lxd
    ):
        """?timeout=N is forwarded to LXD's wait endpoint."""
        mock_lxd.get.return_value = lxd_sync(
            {"id": "op-1", "status": "Success"}
        )

        resp = await client.get(
            "/api/v1/operations/op-1/wait?timeout=5", headers=admin_headers
        )
        assert resp.status_code == 200
        call = mock_lxd.get.call_args
        assert call.kwargs["params"]["timeout"] == 5


@pytest.mark.asyncio
class TestOperationRefBuilding:
    """Unit tests for the operation-ref helper (used by create/delete/etc)."""

    def test_async_ref_includes_our_urls(self):
        from app.services.lxd_operations import to_async_ref

        metadata = {
            "id": "abc-123",
            "operation": "/1.0/operations/abc-123",
            "status": "Running",
        }
        ref = to_async_ref(metadata)
        assert ref.operation_id == "abc-123"
        assert ref.poll_url == "/api/v1/operations/abc-123"
        assert ref.wait_url == "/api/v1/operations/abc-123/wait"

    def test_operation_id_extracted_from_url(self):
        from app.services.lxd_operations import operation_id_from_url

        assert operation_id_from_url("/1.0/operations/op-9") == "op-9"
        assert operation_id_from_url(None) is None
        assert operation_id_from_url("bare-id") == "bare-id"
