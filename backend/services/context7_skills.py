"""
services/context7_skills.py — Context7 (context7.com) doc-fetch tool.
Master Plan "BUILD QUALITY" · Phase S1.

WHY: the LLM's training data goes stale — it writes code against old
Next.js / FastAPI / React APIs. Context7 gives ORA CURRENT, version-
aware library docs it can fetch BEFORE writing a fix, so the fix uses
the right API for the library version actually in play.

Not a duplicate of `services.dev_skills.find_package_docs` (S0
detect-first check, 2026-09-07): that tool returns npm/PyPI REGISTRY
METADATA (latest version, homepage, license) — it never fetches doc
CONTENT. This tool fetches actual usage-focused documentation/API
snippets for writing correct code. Complementary, not overlapping.

API (verified live against context7.com, 2026-09-07 — no SDK exists
for this, pure httpx per the Web Skills / Dev Skills convention):
    Auth:   "Authorization: Bearer {key}" if CONTEXT7_API_KEY is set,
            else NO Authorization header — Context7's free tier
            works keyless (rate-limited); a key just raises the cap.
    Step 1: GET /api/v2/libs/search?libraryName=X   -> resolve libraryId
    Step 2: GET /api/v2/context?libraryId=Y&query=Z -> fetch docs

The tool is OPT-IN — the model decides when to call it (documented in
services/orchestrator.py's system prompt + surfaced via the code-task
tool-namespace-reduction group). Never forced on every turn.

Caching is NOT an optimisation here — it's the safety mechanism that
lets the keyless (rate-limited) free tier actually work: every call
first checks the in-memory cache; only a genuine cache miss hits the
network. A 429 falls back to a stale cache entry if one exists, and
otherwise degrades gracefully (never crashes, never hangs the turn —
same contract as every other tool in this module family, see
web_skills.py's docstring).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

from services.http import ext_client

logger = logging.getLogger(__name__)

CONTEXT7_BASE = "https://context7.com/api/v2"
_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)

_CACHE_TTL_S = 3600.0  # 1h — per founder spec

# libraryName (lowercased) -> (libraryId, expires_at monotonic)
_LIB_ID_CACHE: dict[str, tuple[str, float]] = {}
# (libraryId, query) -> (docs dict, expires_at monotonic)
_DOCS_CACHE: dict[tuple[str, str], tuple[dict, float]] = {}


def _context7_key() -> Optional[str]:
    v = (os.environ.get("CONTEXT7_API_KEY") or "").strip()
    return v or None


def _headers() -> dict:
    key = _context7_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


def clear_caches() -> None:
    """Test-only reset — the module-level dicts persist for process
    lifetime otherwise, which would leak state across test cases."""
    _LIB_ID_CACHE.clear()
    _DOCS_CACHE.clear()


# ── Step 1: resolve libraryId ──────────────────────────────────────────
async def _resolve_library_id(library_name: str) -> tuple[Optional[str], bool, bool]:
    """Returns (library_id_or_None, cache_hit, rate_limited)."""
    key = library_name.strip().lower()
    cached = _LIB_ID_CACHE.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0], True, False

    try:
        async with ext_client("context7", timeout=_TIMEOUT) as c:
            r = await c.get(
                f"{CONTEXT7_BASE}/libs/search",
                params={"libraryName": library_name},
                headers=_headers(),
            )
    except httpx.RequestError as e:
        logger.warning("context7 libs/search network error: %r", e)
        # Stale-but-present cache beats a hard failure even past TTL.
        if cached:
            return cached[0], True, False
        return None, False, False

    if r.status_code == 429:
        if cached:
            return cached[0], True, True
        return None, False, True
    if r.status_code >= 400:
        logger.warning("context7 libs/search HTTP %s: %s", r.status_code, r.text[:200])
        return None, False, False

    data = r.json() or {}
    results = data.get("results") or data.get("libraries") or []
    if not results:
        return None, False, False
    library_id = results[0].get("id") or results[0].get("libraryId")
    if not library_id:
        return None, False, False
    _LIB_ID_CACHE[key] = (library_id, now + _CACHE_TTL_S)
    return library_id, False, False


# ── Step 2: fetch docs context ─────────────────────────────────────────
async def _fetch_context(library_id: str, query: str) -> tuple[Optional[dict], bool, bool]:
    """Returns (docs_or_None, cache_hit, rate_limited)."""
    cache_key = (library_id, query.strip().lower())
    cached = _DOCS_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0], True, False

    try:
        async with ext_client("context7", timeout=_TIMEOUT) as c:
            r = await c.get(
                f"{CONTEXT7_BASE}/context",
                params={"libraryId": library_id, "query": query, "type": "txt"},
                headers=_headers(),
            )
    except httpx.RequestError as e:
        logger.warning("context7 context fetch network error: %r", e)
        if cached:
            return cached[0], True, False
        return None, False, False

    if r.status_code == 429:
        if cached:
            return cached[0], True, True
        return None, False, True
    if r.status_code >= 400:
        logger.warning("context7 context HTTP %s: %s", r.status_code, r.text[:200])
        return None, False, False

    # `type=txt` returns plain text; the API also supports a JSON
    # envelope — accept either so a future flip doesn't break us.
    ctype = (r.headers.get("content-type") or "").lower()
    if "json" in ctype:
        body = r.json() or {}
        text = body.get("content") or body.get("text") or ""
    else:
        text = r.text
    docs = {"content": text[:12_000], "truncated": len(text) > 12_000}
    _DOCS_CACHE[cache_key] = (docs, now + _CACHE_TTL_S)
    return docs, False, False


# ── Cost ledger (lightweight — NOT the token/cost LLM shape) ───────────
async def log_context7_call(
    *, library: str, library_id: Optional[str], cache_hit: bool,
    latency_ms: int, rate_limited: bool,
) -> None:
    """Best-effort row in `context7_calls` — call count + latency +
    cache-hit + rate-limited, never token/cost. Never raises."""
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is None:
            return
        await db.context7_calls.insert_one({
            "library":       library,
            "library_id":    library_id,
            "cache_hit":     bool(cache_hit),
            "latency_ms":    int(latency_ms),
            "rate_limited":  bool(rate_limited),
            "cost_usd":      0.0,  # Context7 free tier / not billed per-call today
            "ts":            time.time(),
        })
    except Exception as e:  # noqa: BLE001 — logging must never break the tool
        logger.debug("log_context7_call failed (non-blocking): %r", e)


# ── The tool itself ─────────────────────────────────────────────────────
async def get_library_docs(ctx: dict, args: dict) -> dict:
    """Fetch CURRENT, version-aware docs for a third-party library from
    Context7 before writing code that uses it. See CONTEXT7_TOOL_SPECS
    below for the LLM-facing description of WHEN to call this."""
    library = ((args or {}).get("library") or (args or {}).get("libraryName") or "").strip()
    query = ((args or {}).get("query") or "").strip()
    if not library:
        return {"ok": False, "error": "Missing required arg `library`"}

    t0 = time.monotonic()
    library_id, id_cache_hit, id_rate_limited = await _resolve_library_id(library)
    if not library_id:
        latency_ms = int((time.monotonic() - t0) * 1000)
        await log_context7_call(
            library=library, library_id=None, cache_hit=id_cache_hit,
            latency_ms=latency_ms, rate_limited=id_rate_limited,
        )
        if id_rate_limited:
            return {
                "ok": False, "rate_limited": True,
                "note": "docs fetch rate-limited — proceeding with my own knowledge",
            }
        return {"ok": False, "error": f"No Context7 library match for '{library}'"}

    docs, docs_cache_hit, docs_rate_limited = await _fetch_context(
        library_id, query or library,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    cache_hit = id_cache_hit and docs_cache_hit
    rate_limited = id_rate_limited or docs_rate_limited
    await log_context7_call(
        library=library, library_id=library_id, cache_hit=cache_hit,
        latency_ms=latency_ms, rate_limited=rate_limited,
    )

    if docs is None:
        if rate_limited:
            return {
                "ok": False, "rate_limited": True,
                "note": "docs fetch rate-limited — proceeding with my own knowledge",
            }
        return {"ok": False, "error": f"Context7 returned no docs for '{library}'"}

    return {
        "ok": True,
        "library": library,
        "library_id": library_id,
        "content": docs["content"],
        "truncated": docs["truncated"],
        "cache_hit": cache_hit,
        "rate_limited": rate_limited,
    }


CONTEXT7_TOOLS = {"get_library_docs": get_library_docs}


CONTEXT7_TOOL_SPECS = [
    {
        "name": "get_library_docs",
        "description": (
            "Fetch CURRENT, version-aware documentation for a third-party "
            "library/framework (React, FastAPI, Express, Prisma, Tailwind, "
            "Django, etc.) via Context7. Call this BEFORE writing code that "
            "uses an external library, so you use the library's CURRENT API "
            "instead of a stale one from training. Do NOT call for stdlib-"
            "only code, the user's OWN project code, or trivial code with no "
            "third-party dependency — this is opt-in, only when about to "
            "write code against an external package. Different from "
            "find_package_docs (which only returns registry metadata like "
            "latest version/license, not doc content)."
        ),
        "args_spec": {
            "library": "string — the package/library name, e.g. 'fastapi', 'react'",
            "query":   "optional string — natural-language question narrowing the "
                       "docs, e.g. 'how to add middleware'. Defaults to `library`.",
        },
    },
]


__all__ = [
    "CONTEXT7_TOOLS", "CONTEXT7_TOOL_SPECS",
    "get_library_docs", "log_context7_call", "clear_caches",
]
