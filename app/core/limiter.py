"""Shared slowapi limiter instance.

Routes decorate with `@limiter.limit(...)` using this single object, and
`main.py` attaches it to `app.state.limiter`. Keeping it in one place avoids
the bug of decorating with one instance and registering another on the app.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

__all__ = ["limiter"]
