"""LXD REST API client.

This module is the *only* place that talks to LXD. It wraps the LXD REST API
using raw `httpx` requests — no `pylxd` or other LXD SDK — exactly mirroring
how LXD's own `lxc` CLI works.

== Two connection modes ==

* **local** — LXD exposes a Unix socket (`/var/snap/lxd/common/lxd/unix.socket`
  for snap, `/var/lib/lxd/unix.socket` for native installs). We talk to it via
  `httpx`'s Unix-socket transport, base URL `http+unix://`. No credentials are
  needed: anything that can write to the socket is trusted.

* **remote** — LXD's network API speaks HTTPS with **mutual TLS**: the client
  presents its own cert/key and the server must have it in its trust store
  (this is what `lxc remote add` does — it exchanges certs). We therefore pass
  `cert=(client.crt, client.key)` to httpx.

== API versioning ==

Every endpoint lives under `/1.0/...`. LXD exposes supported versions at `/`;
we hard-code `/1.0` (the current stable major version) which matches what `lxc`
does in practice.

== The three response shapes ==

LXD returns one of (see schemas/lxd.py):

  * `type: sync`  — done; `metadata` is the payload.
  * `type: async` — accepted as a background operation (HTTP 202) with an
    `operation` URL. The caller must poll/wait/subscribe. `request()` raises
    nothing here; it returns the operation reference via `metadata`.
  * `type: error` — failed immediately; we raise the matching LXDError.

== Why async operations matter ==

Creating an instance, copying an image, snapshotting, migrating, etc. can take
seconds to *minutes*. LXD therefore returns 202 + an operation id right away.
If we blocked the HTTP request until completion we'd tie up a worker and risk
client timeouts. Instead this client surfaces the operation reference so the
route layer can return it and let the client poll (`operations.py`).
"""

from __future__ import annotations

import logging
import os
import ssl
from typing import Any, AsyncIterator, Optional
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.schemas.lxd import LXDResponse
from app.services.exceptions import (
    LXDConnectionError,
    LXDError,
    LXDOperationError,
    lxd_error_for_status,
)

logger = logging.getLogger(__name__)

# The LXD API major version path prefix (stable since 2017).
API_PREFIX = "/1.0"


class LXDClient:
    """Async client for the LXD REST API (local socket or remote TLS).

    A single shared instance is exposed via `get_lxd_client()`; it lazily
    builds the underlying `httpx.AsyncClient` (with the right transport) the
    first time it's used.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._mode: str = settings.LXD_CONNECTION_MODE

    # ---- transport / client construction ----------------------------------
    def _build_transport(self) -> httpx.AsyncBaseTransport:
        """Build the httpx transport for the configured connection mode."""
        if self._mode == "local":
            socket_path = settings.LXD_SOCKET_PATH
            if not os.path.exists(socket_path):
                # We still construct the transport; the real check happens on
                # first request via /health, but warn loudly at construction.
                logger.warning("LXD socket not found at %s", socket_path)
            # httpx supports `http+unix://` natively via its transport.
            return httpx.AsyncHTTPTransport(uds=socket_path)
        # remote: standard HTTPS transport. mTLS cert is attached on the client.
        return httpx.AsyncHTTPTransport()

    def _build_client(self) -> httpx.AsyncClient:
        """Construct the underlying httpx.AsyncClient with the right auth."""
        transport = self._build_transport()

        if self._mode == "local":
            # httpx decodes `http+unix://` + percent-encoded socket path. We use
            # a plain `http://` base with a Unix transport.
            base_url = "http://unix"
            return httpx.AsyncClient(
                base_url=base_url,
                transport=transport,
                timeout=settings.LXD_TIMEOUT,
            )

        # ---- remote (mTLS) ----
        cert: tuple[str, str] | None = None
        if settings.LXD_CLIENT_CERT_PATH and settings.LXD_CLIENT_KEY_PATH:
            cert = (settings.LXD_CLIENT_CERT_PATH, settings.LXD_CLIENT_KEY_PATH)

        verify: ssl.SSLContext | bool
        if settings.LXD_TRUSTED_CA_PATH and os.path.exists(
            settings.LXD_TRUSTED_CA_PATH
        ):
            ctx = ssl.create_default_context(cafile=settings.LXD_TRUSTED_CA_PATH)
            verify = ctx
        else:
            # Self-signed LXD server with no CA provided: caller accepts the
            # risk (typical for dev). Logged at WARNING.
            logger.warning(
                "LXD_TRUSTED_CA_PATH not set/missing — disabling TLS "
                "verification for remote connection (NOT for production)."
            )
            verify = False

        return httpx.AsyncClient(
            base_url=settings.LXD_REMOTE_URL,
            transport=transport,
            cert=cert,
            verify=verify,
            timeout=settings.LXD_TIMEOUT,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazily build and cache the underlying httpx client."""
        if self._client is None:
            self._client = self._build_client()
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client (call on app shutdown if needed)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- query parameter helpers ------------------------------------------
    @staticmethod
    def _build_params(
        *,
        recursion: Optional[int] = None,
        filter_expr: Optional[str] = None,
        project: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Assemble LXD query params.

        * `recursion=1` — ask LXD to inline full objects instead of returning a
          list of URLs. We expose this to clients as `?expand=true`.
        * `filter` — LXD's OData-style filter (`status eq Running`). Passed
          through verbatim from the client's `?filter=`.
        * `project` — LXD's namespace switcher; included on every request that
          honors it so a single header/query can scope a whole request.
        """
        params: dict[str, Any] = {}
        if recursion is not None:
            params["recursion"] = recursion
        if filter_expr:
            params["filter"] = filter_expr
        if project:
            params["project"] = project
        if extra:
            params.update(extra)
        return params

    # ---- core request method ---------------------------------------------
    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[dict[str, Any]] = None,
        content: bytes | None = None,
        headers: Optional[dict[str, str]] = None,
        raw_response: bool = False,
        timeout: float | None = None,
    ) -> Any:
        """Perform a request against the LXD REST API and normalize the result.

        - `path` is relative to `/1.0` (e.g. `"instances"` -> `/1.0/instances`).
          Pass a leading-slash absolute path (e.g. `"/1.0")` to hit endpoints
          outside the standard prefix (like server info).
        - On a **sync** response: returns `metadata` (the payload).
        - On an **async** response: returns the full parsed envelope so callers
          that need the operation URL can read it (they typically call
          `async_operation_ref()` afterwards).
        - On an **error** response: raises the matching LXDError subclass.
        - If `raw_response=True`: returns the raw `httpx.Response` (used for
          backup file downloads / non-JSON bodies).

        Network/transport failures become `LXDConnectionError` (HTTP 503).
        """
        url = path if path.startswith("/") else f"{API_PREFIX}/{path}"
        try:
            response = await self.client.request(
                method,
                url,
                json=json_body,
                params=params,
                content=content,
                headers=headers,
                timeout=timeout if timeout is not None else settings.LXD_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.warning("LXD connection error: %s", exc)
            raise LXDConnectionError(
                f"Cannot reach LXD ({self._mode} mode): {exc}"
            ) from exc

        if raw_response:
            return response

        # Some endpoints (backup export) return binary/empty bodies; guard.
        if not response.content:
            return None

        try:
            envelope = LXDResponse.model_validate(response.json())
        except Exception as exc:  # non-JSON or malformed
            raise LXDError(
                f"LXD returned a non-JSON response (HTTP {response.status_code}): "
                f"{response.text[:200]}",
                status_code=response.status_code,
            ) from exc

        if envelope.is_error:
            code = envelope.error_code or response.status_code
            raise lxd_error_for_status(
                envelope.error or "LXD error", code
            )

        if envelope.is_async:
            # Don't raise — surface the operation. Caller decides whether to
            # poll/wait or just return the reference. We return metadata which
            # for async responses IS the operation object.
            return envelope.metadata if envelope.metadata is not None else {
                "operation": envelope.operation,
                "status": envelope.status,
            }

        # sync — return the payload.
        return envelope.metadata

    # ---- convenience HTTP verbs ------------------------------------------
    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(
        self, path: str, *, json_body: Any = None, **kwargs: Any
    ) -> Any:
        return await self.request("POST", path, json_body=json_body, **kwargs)

    async def put(
        self, path: str, *, json_body: Any = None, **kwargs: Any
    ) -> Any:
        return await self.request("PUT", path, json_body=json_body, **kwargs)

    async def patch(
        self, path: str, *, json_body: Any = None, **kwargs: Any
    ) -> Any:
        return await self.request("PATCH", path, json_body=json_body, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, **kwargs)

    # ---- WebSocket URL builder -------------------------------------------
    def ws_url(self, path: str, *, secret: str | None = None) -> str:
        """Build a WebSocket URL for the LXD API (exec/console/events).

        LXD's exec & console endpoints return operation secrets; the client
        then opens WebSockets at `/1.0/operations/<id>/websocket?secret=<s>`
        (exec uses 3 secrets: control/0/1, console uses one). This builds the
        correct URL for the active connection mode:

        * local  -> `unix://` style ws over the socket (handled by the `ws://`
          path through our proxy, see operations/exec routes).
        * remote -> `wss://<host>:<port>/1.0/...` with the client cert.
        """
        url = path if path.startswith("/") else f"{API_PREFIX}/{path}"
        if self._mode == "remote":
            base = settings.LXD_REMOTE_URL.replace("https://", "wss://").replace(
                "http://", "ws://"
            )
            full = f"{base}{url}"
        else:
            # Local sockets aren't directly WebSocket-able from a browser; we
            # always proxy these through our own /ws endpoints (see routes).
            # This URL is consumed by our server-side ws relay only.
            full = f"http://unix{url}"
        if secret:
            full = f"{full}?secret={quote(secret, safe='')}"
        return full


# ---- module-level singleton + dependency -----------------------------------
_client_singleton: LXDClient | None = None


def get_lxd_client() -> LXDClient:
    """Return the process-wide LXDClient singleton.

    Used both directly by route handlers and as a FastAPI dependency
    (`Depends(get_lxd_client)`), so tests can override it via
    `app.dependency_overrides`.
    """
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = LXDClient()
    return _client_singleton


def set_lxd_client_for_testing(client: LXDClient | None) -> None:
    """Inject (or clear) the singleton — for tests only."""
    global _client_singleton
    _client_singleton = client


__all__ = [
    "API_PREFIX",
    "LXDClient",
    "get_lxd_client",
    "set_lxd_client_for_testing",
]
