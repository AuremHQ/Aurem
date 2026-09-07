"""
services/repo_status_cache.py — the short-TTL per-project reachability
cache behind GET /cto/projects/{id}/repo-status, plus `invalidate()`.
Extracted from routers/repo_status.py (2026-09-07) so services
(loop_engine post-ship, repo_heal) can invalidate it without importing
the router layer. routers/repo_status.py re-exports `_CACHE` /
`_CACHE_TTL_S` / `invalidate` so existing callers/tests are unchanged.
"""
from __future__ import annotations

_CACHE_TTL_S    = 8       # short cache to swallow duplicate polls
_CACHE: dict[str, dict] = {}        # keyed by project_id


def invalidate(project_id: str) -> None:
    """B1 hardening (2026-08-30) — drop the cached row for one project
    so the very next poll re-checks GitHub instead of replaying a stale
    reading. Call this right after an action that's known to change
    reachability (e.g. a successful ship/commit) so the "GitHub App not
    connected" banner can't keep showing a pre-ship reading for up to
    `_CACHE_TTL_S` seconds after the connection has proven itself by
    actually landing a commit. Best-effort / non-fatal by design — a
    missing cache entry just means the next poll does a fresh check."""
    _CACHE.pop(project_id, None)
