"""LXD-specific exception hierarchy.

These map LXD REST API error responses onto structured exceptions that the
FastAPI routes translate into HTTP status codes (see `api/deps.py` /
route-level handling). Keeping them distinct from generic Python exceptions
means an unexpected `ValueError` bubbles up as a 500 while an
`LXDNotFoundError` becomes a clean 404.
"""

from __future__ import annotations


class LXDError(Exception):
    """Base class for all LXD-related errors.

    Attributes:
        message: human-readable detail from LXD (or a generated message).
        status_code: the HTTP status LXD returned (0 if we never reached LXD).
    """

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


# --- Connectivity -----------------------------------------------------------
class LXDConnectionError(LXDError):
    """Cannot reach the LXD daemon (socket missing / TLS failure / timeout).

    Maps to HTTP 503 — LXD is effectively down from our point of view.
    """


# --- HTTP-level errors (raised from LXD `type: error` envelopes) ------------
class LXDRequestError(LXDError):
    """A non-success error envelope returned by LXD.

    `status_code` is the HTTP status LXD used. Subclasses below specialize the
    common ones so routes can catch the most useful type.
    """


class LXDBadRequest(LXDRequestError):
    """LXD rejected the request (400)."""


class LXDAuthError(LXDRequestError):
    """LXD refused authentication / authorization (401/403).

    In remote (mTLS) mode this usually means the client cert is not trusted.
    """


class LXDNotFoundError(LXDRequestError):
    """LXD reported the resource does not exist (404)."""


class LXDConflictError(LXDRequestError):
    """LXD reported a conflict, e.g. name already in use (409)."""


# --- Async operation lifecycle ----------------------------------------------
class LXDOperationError(LXDError):
    """An async operation completed but ended in *failure*.

    LXD represents long-running work (create instance, copy image, ...) as an
    operation that can finish Running -> Success OR Running -> Failure. This is
    raised when polling/waiting reveals a failure, so a route can return it as
    a 500 (or a 422) with the operation's error text.
    """


def lxd_error_for_status(message: str, status_code: int) -> LXDRequestError:
    """Pick the most specific exception subclass for an HTTP status code."""
    if status_code == 400:
        return LXDBadRequest(message, status_code)
    if status_code in (401, 403):
        return LXDAuthError(message, status_code)
    if status_code == 404:
        return LXDNotFoundError(message, status_code)
    if status_code == 409:
        return LXDConflictError(message, status_code)
    return LXDRequestError(message, status_code)
