"""
services/repo_text_cache.py — build the {path: text} cache for a repo
(tree walk + parallel blob fetch, tree-SHA-keyed persistent cache).
Extracted verbatim from routers/codebase_health.py (2026-09-07) so
services/project_onboarding_scan.py stops importing a router.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from services.github_repo_fetch import (
    _list_repo_tree_with_sha, _fetch_file,
    _MAX_FILES, _MAX_BYTES_PER_FILE, _SCAN_EXTS, _SKIP_DIRS,
    _CONCURRENT_FETCHES,
)
from services.codebase_health_core import _is_dockerfile
from services.http import ext_client
from services.scan_cache import (
    get_cached_text_cache, put_cached_text_cache,
)

logger = logging.getLogger(__name__)


async def build_text_cache(owner: str, repo: str, pat: str) -> dict[str, str]:
    """Walk the repo tree + fetch every scannable file.  Cached for the
    duration of a single /scan request so all 5 categories share the
    same fetch budget.

    Iter 212m-79 — also checks Redis for a previously-built bundle
    keyed on `owner/repo@tree_sha`.  Cross-pod cache hits skip the
    ~50-600 GitHub calls entirely (~60 s saved on large repos).  TTL
    24 h; key invalidates automatically on the next commit because the
    tree SHA changes.

    Iter 212m-221 — Two hardening changes to root-cause the
    intermittent 1.3s Cloudflare 502 on prod:
      * Explicit `Timeout(45)` on the outer `AsyncClient` — the old
        default-None meant a single stalled GH connection could hold
        the pod's event loop indefinitely, triggering Cloudflare's
        origin-idle-timeout intercept.
      * Structured latency log on every call (`scan.text_cache
        owner=… repo=… sha=… hit=… files=… ms=…`) so a future 502
        can be traced from the log stream alone.
    """
    import time as _time
    _t0 = _time.time()
    _hit  = False
    _files = 0

    _timeout = httpx.Timeout(45.0, connect=6.0, read=15.0)
    async with ext_client("github", timeout=httpx.Timeout(45.0, connect=6.0, read=15.0)) as client:
        blobs, tree_sha = await _list_repo_tree_with_sha(
            client, owner, repo, pat,
        )

        # ── Redis-backed dedup lookup ──────────────────────────────
        if tree_sha:
            cached = await get_cached_text_cache(owner, repo, tree_sha)
            if cached is not None:
                # Hit — skip GitHub entirely.  Re-apply the path
                # candidate filter in case _SCAN_EXTS changed between
                # writes (cheap; pure-Python loop over keys).
                filtered: dict[str, str] = {}
                for path, txt in cached.items():
                    if not path:
                        continue
                    if any(p in _SKIP_DIRS for p in path.split("/")):
                        continue
                    lower = path.lower()
                    if not (any(lower.endswith(ext) for ext in _SCAN_EXTS)
                            or lower.endswith("requirements.txt")
                            or lower.endswith("package.json")
                            or _is_dockerfile(lower)):
                        continue
                    filtered[path] = txt
                _hit  = True
                _files = len(filtered)
                logger.info(
                    "scan.text_cache owner=%s repo=%s sha=%s hit=1 "
                    "files=%d ms=%d",
                    owner, repo, tree_sha[:7], _files,
                    int((_time.time() - _t0) * 1000),
                )
                return filtered

        text_cache: dict[str, str] = {}
        candidates: list[dict] = []
        for b in blobs:
            path = b.get("path", "")
            if not path:
                continue
            if any(p in _SKIP_DIRS for p in path.split("/")):
                continue
            lower = path.lower()
            if not (any(lower.endswith(ext) for ext in _SCAN_EXTS)
                    or lower.endswith("requirements.txt")
                    or lower.endswith("package.json")
                    or _is_dockerfile(lower)):
                continue
            if b.get("size", 0) > _MAX_BYTES_PER_FILE:
                continue
            candidates.append(b)
            if len(candidates) >= _MAX_FILES:
                break

        sem = asyncio.Semaphore(_CONCURRENT_FETCHES)

        async def _one(blob):
            async with sem:
                t = await _fetch_file(client, owner, repo, blob["path"], pat)
            if t:
                text_cache[blob["path"]] = t

        await asyncio.gather(*[_one(b) for b in candidates])

        # ── Best-effort write-back; never blocks the response ─────
        if tree_sha and text_cache:
            try:
                await put_cached_text_cache(owner, repo, tree_sha, text_cache)
            except Exception as e:
                logger.debug("scan_cache put_cached failed: %r", e)

    _files = len(text_cache)
    logger.info(
        "scan.text_cache owner=%s repo=%s sha=%s hit=0 files=%d "
        "candidates=%d ms=%d",
        owner, repo, (tree_sha or "-")[:7], _files, len(candidates),
        int((_time.time() - _t0) * 1000),
    )
    return text_cache
