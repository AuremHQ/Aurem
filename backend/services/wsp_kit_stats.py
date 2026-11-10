"""
services/wsp_kit_stats.py — live GitHub stars/forks for the wsp-kit
research repo (AuremHQ/weight-space-programming), shown on /harness.

Per the approved verified facts: "stars and forks come from the
public GitHub API... clones and views return 401 without owner
login" — so ONLY stars/forks are ever fetched live here; clones/views
stay a dated snapshot baked directly into the harness content doc
(services/harness_content_repo.py), never refetched.

Public, UNAUTHENTICATED GitHub REST call (no token needed/used) —
cached 5 minutes so normal traffic never approaches GitHub's 60
req/hour unauthenticated cap.

Fails closed: any error returns the last good cached value, or None
if nothing has ever succeeded — the frontend hides the live badge
rather than showing a stale guess or a fabricated number.
"""
from __future__ import annotations

import logging
import time

import httpx

from services.http import ext_client

logger = logging.getLogger("aurem-dev.wsp_kit_stats")

_REPO_OWNER = "AuremHQ"
_REPO_NAME = "weight-space-programming"
_CACHE_TTL_S = 300

_cache: dict = {"data": None, "ts": 0.0}


async def fetch_wsp_kit_live_stats() -> dict | None:
    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL_S:
        return _cache["data"]
    try:
        async with ext_client(
            "github",
            timeout=httpx.Timeout(10.0),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=5),
        ) as client:
            r = await client.get(
                f"https://api.github.com/repos/{_REPO_OWNER}/{_REPO_NAME}",
                headers={"Accept": "application/vnd.github+json"},
            )
            r.raise_for_status()
            body = r.json()
            data = {
                "stars": body.get("stargazers_count"),
                "forks": body.get("forks_count"),
            }
    except Exception as e:                                       # noqa: BLE001
        logger.warning("fetch_wsp_kit_live_stats failed (fail-closed): %r", e)
        return _cache["data"]
    _cache["data"] = data
    _cache["ts"] = now
    return data
