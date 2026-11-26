"""
services/route_cache.py  —  Iter 118 simple in-memory route cache.

Targets 5 high-frequency polling endpoints. Reduces DB query load by ~12x
on the wall + stats + admin telemetry endpoints which are polled every
~5s by every open admin tab.

Rules (confirmed with founder Feb 2026):
  - Cache is keyed by (path, sorted query string). Auth header is NOT
    part of the key — these endpoints all return GLOBAL aggregates
    (same data for any caller).
  - Admin endpoints still require admin auth — the middleware does a
    JWT pre-check BEFORE serving a cache hit. This prevents anon
    callers from reading cached admin data.
  - Only 200 responses are cached.
  - TTL-only invalidation by default (stale data for at most
    30-60s is acceptable for stats) — PLUS a manual purge since
    Part XV: `invalidate()` below, wired to
    POST /admin/cache/route-invalidate and included in
    POST /admin/cache/purge (routers/admin_ops_config.py), so a
    founder can force a refetch instead of waiting out the TTL.
  - Process-local dict, single worker. If we ever scale to multiple
    uvicorn workers, swap this out for redis.
"""

from __future__ import annotations
import time
from typing import Dict, Tuple, Optional

# path → (ttl_seconds, requires_admin)
ROUTE_CONFIG: Dict[str, Tuple[int, bool]] = {
    "/api/aurem-dev/usage/public/stats":      (60, False),
    "/api/aurem-dev/wall/stats":              (60, False),
    "/api/aurem-dev/wall/feed":               (30, False),
    "/api/aurem-dev/admin/council/stats":     (60, True),
    "/api/aurem-dev/admin/mode-telemetry":    (60, True),
    # Iter 140 — extend cache to the heavy aggregate routes that the
    # admin dashboard polls on a tab open. Public trust pages get a
    # short TTL since deploy/uptime numbers don't churn.
    "/api/aurem-dev/admin/product-analytics": (120, True),
    "/api/aurem-dev/admin/db-health":         (30,  True),
    "/api/aurem-dev/admin/architecture":      (300, True),
    "/api/aurem-dev/admin/skills-usage":      (120, True),
    "/api/aurem-dev/trust/uptime":            (60,  False),
    "/api/aurem-dev/trust/deploy-count":      (300, False),
}

# Legacy alias kept so existing imports / lint don't break.
ROUTE_TTL: Dict[str, int] = {p: c[0] for p, c in ROUTE_CONFIG.items()}

# key → (expires_at_unix, status_code, body_bytes, content_type)
_CACHE: Dict[str, Tuple[float, int, bytes, str]] = {}


def make_key(path: str, query_string: str) -> str:
    """Cache key = path + sorted query string."""
    if not query_string:
        return path
    parts = sorted(query_string.split("&"))
    return f"{path}?{'&'.join(parts)}"


def get(key: str) -> Optional[Tuple[int, bytes, str]]:
    """Return (status, body, content_type) on hit, None on miss/expired."""
    entry = _CACHE.get(key)
    if entry is None:
        return None
    expires_at, status, body, ctype = entry
    if time.time() >= expires_at:
        _CACHE.pop(key, None)
        return None
    return status, body, ctype


def put(key: str, ttl: int, status: int, body: bytes, content_type: str) -> None:
    _CACHE[key] = (time.time() + ttl, status, body, content_type)


def clear() -> None:
    """Test hook — flush the cache between unit tests."""
    _CACHE.clear()


def invalidate(path: str | None = None) -> int:
    """Manual purge (Part XV) — returns the number of entries dropped.

    `path=None` drops every entry. Otherwise drops all entries for
    that one route: the bare path plus every query-string variant
    (`path` or `path?...`), so invalidating
    `/api/aurem-dev/admin/council/stats` refetches it on the next
    request no matter which query string was cached.
    """
    if path is None:
        n = len(_CACHE)
        _CACHE.clear()
        return n
    doomed = [k for k in _CACHE if k == path or k.startswith(path + "?")]
    for k in doomed:
        _CACHE.pop(k, None)
    return len(doomed)


def size() -> int:
    return len(_CACHE)
