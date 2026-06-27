"""Lightweight pagination helpers applied on top of LXD responses.

LXD does its own filtering/recursion; we add simple `limit`/`offset` slicing
*after* retrieving the (possibly already-filtered) list, so API clients get a
consistent pagination contract regardless of which LXD endpoint is behind it.
"""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import Query
from pydantic import BaseModel


class PageParams(BaseModel):
    """Standard limit/offset page parameters."""

    limit: int = 100
    offset: int = 0


def page_params(
    limit: int = Query(100, ge=1, le=1000, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
) -> PageParams:
    """FastAPI dependency parsing `?limit=` / `?offset=`."""
    return PageParams(limit=limit, offset=offset)


def paginate(items: Sequence[Any], params: PageParams) -> dict[str, Any]:
    """Slice `items` by limit/offset and return a page envelope.

    Returns: ``{"items": [...], "limit": n, "offset": m, "total": N}``.
    """
    total = len(items)
    start = params.offset
    end = start + params.limit
    page = list(items[start:end])
    return {
        "items": page,
        "limit": params.limit,
        "offset": params.offset,
        "total": total,
    }
