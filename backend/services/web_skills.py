"""
services/web_skills.py — Web skills for ORA.

Closes the "ORA has no internet" gap. Five skills wired into the
orchestrator's tool-call layer (see services/local_tools.py):

  • web_search                  Tavily /search — Google-style top-N results
  • fetch_url                   Tavily /extract — clean text for any URL
  • web_search_and_summarize    /search with include_answer=True (Tavily's
                                own 1-line summary across the result set)
  • firecrawl_scrape            Firecrawl /v1/scrape — JS-heavy pages,
                                PDFs, screenshots
  • firecrawl_crawl_site        Firecrawl /v1/crawl — full domain crawl
                                (async + polled)

All keys live in os.environ. Missing key → clean `{"ok": False,
"error": "..."}` (never crash, never raise into the orchestrator).
Pure httpx — no new SDKs added.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Optional

import httpx

from services.http import ext_client
from services.url_guard import is_safe_public_http_url

logger = logging.getLogger(__name__)

TAVILY_BASE = "https://api.tavily.com"
FIRECRAWL_BASE = "https://api.firecrawl.dev/v1"

TAVILY_TIMEOUT = 15.0     # per playbook hard cap
FIRECRAWL_SCRAPE_TIMEOUT = 60.0
FIRECRAWL_CRAWL_POLL_MAX = 90.0   # total seconds we'll wait for /crawl

URL_RE = re.compile(r"https?://[^\s\"'<>]+")


# 2026-11-25 — ORA audit Part IX D(b)/D(d): arg parsing + JSON parsing
# helpers. LLM-supplied args arrive as untyped JSON: a `null` query,
# an int where a string belongs, or "abc" where an int belongs used
# to raise OUTSIDE the try blocks (AttributeError/ValueError), and
# `r.json()` on a non-JSON 200 body raised JSONDecodeError — both
# violating this module's "never crash, never raise" contract.
def _safe_str(value: Any, default: str = "") -> str:
    """str value, stripped — "" for None/non-str (never raises)."""
    return value.strip() if isinstance(value, str) else default


def _safe_int(value: Any, default: int, lo: int, hi: int) -> int:
    """int(value) clamped to [lo, hi] — `default` on TypeError /
    ValueError (e.g. "abc", "5.5", ["x"])."""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))


def _safe_json(r: Any, vendor: str) -> tuple[Optional[dict], Optional[dict]]:
    """Parse an httpx response body as a JSON OBJECT. Returns
    (data, None) on success, (None, error_dict) on a non-JSON body
    or a non-object body (e.g. a JSON list — the old code then
    crashed on data.get). Never raises."""
    try:
        data = r.json()
    except ValueError:
        return None, {"ok": False,
                      "error": f"{vendor} returned a non-JSON response"}
    if not isinstance(data, dict):
        return None, {"ok": False,
                      "error": f"{vendor} returned a non-JSON-object response"}
    return data, None


# 2026-11-09 — devil's-advocate audit: this used to be a hand-rolled,
# hostname-STRING-only blocklist (no DNS resolution at all), despite
# its own comment claiming to reuse url_fetcher.py's guard. A hostname
# that isn't itself "localhost"/a literal private IP sailed through
# even if it resolved to one — the exact DNS-rebinding class
# services.url_guard (built for the preview_capture.py SSRF fix, C8)
# exists to catch. fetch_url/firecrawl_scrape/firecrawl_crawl_site are
# real LLM-invokable chat tools, and orchestrator.py auto-invokes
# fetch_url on any URL found in the raw user prompt BEFORE the LLM
# even runs — so this guard is reachable with nothing more than
# pasting a URL into chat. Now actually reuses the real, DNS-
# resolving, fail-closed guard instead of a second implementation.


def _tavily_key() -> str | None:
    return (os.environ.get("TAVILY_API_KEY") or "").strip() or None


def _firecrawl_key() -> str | None:
    return (os.environ.get("FIRECRAWL_API_KEY") or "").strip() or None


# ── 1. web_search (Tavily /search) ────────────────────────────────────
async def web_search(ctx: dict, args: dict) -> dict:
    """Top-N Google-style results for a query."""
    key = _tavily_key()
    if not key:
        return {"ok": False, "error": "TAVILY_API_KEY not configured on server"}
    query = _safe_str((args or {}).get("query"))
    if not query:
        return {"ok": False, "error": "Missing required arg `query`"}
    max_results = _safe_int((args or {}).get("max_results"), 5, 1, 10)
    search_depth = "advanced" if (args or {}).get("deep") else "basic"

    payload = {
        "query": query[:380],
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "topic": (args or {}).get("topic") or "general",
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        from services.retry_guard import get_breaker
        _br = get_breaker("tavily")
        if not _br.allow():
            return {"ok": False, "error": "Web search temporarily unavailable "
                    f"(tavily circuit open — retry in ~{_br.retry_after_s():.0f}s)"}
        # Phase 3 · Custom-breaker reconciliation (2026-02-12) — migrated to
        # ext_client("tavily", ...) for uniform header injection + safety.
        # The explicit get_breaker("tavily") block above is PRESERVED because
        # ext_client's CM form does NOT auto-apply retries or the breaker
        # (see services/http/client.py docstring lines 122-124). Semantics
        # unchanged: manual _br.allow() gate, _br.record_failure() /
        # _br.record_success() on outcome.
        async with ext_client("tavily", timeout=httpx.Timeout(TAVILY_TIMEOUT)) as c:
            r = await c.post(f"{TAVILY_BASE}/search", json=payload, headers=headers)
    except httpx.TimeoutException:
        _br.record_failure("timeout")
        return {"ok": False, "error": "Tavily search timed out after 15s"}
    except httpx.RequestError as e:
        _br.record_failure(repr(e))
        return {"ok": False, "error": f"Tavily search network error: {e}"}

    if r.status_code == 429:
        return {"ok": False, "error": "Tavily rate-limited (429)",
                "retry_after": r.headers.get("retry-after")}
    if r.status_code >= 400:
        if r.status_code >= 500:
            _br.record_failure(f"tavily_status_{r.status_code}")
        return {"ok": False, "error": f"Tavily {r.status_code}: {r.text[:300]}"}
    _br.record_success()

    data, _jerr = _safe_json(r, "Tavily")
    if _jerr:
        return _jerr
    results = []
    _rows = data.get("results") or []
    if not isinstance(_rows, list):
        _rows = []
    for row in _rows[:max_results]:
        results.append({
            "title": row.get("title") or "",
            "url":   row.get("url") or "",
            "snippet": (row.get("content") or "")[:600],
            "score": row.get("score"),
        })
    return {
        "ok": True,
        "query": query,
        "results": results,
        "count": len(results),
        "depth": search_depth,
    }


# ── 2. fetch_url (Tavily /extract) ────────────────────────────────────
async def fetch_url(ctx: dict, args: dict) -> dict:
    """Clean text content for a single URL (or up to 5)."""
    key = _tavily_key()
    if not key:
        return {"ok": False, "error": "TAVILY_API_KEY not configured on server"}

    raw_urls = (args or {}).get("urls") or (args or {}).get("url")
    if isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    if not isinstance(raw_urls, list) or not raw_urls:
        return {"ok": False, "error": "Missing required arg `url` or `urls`"}

    urls = []
    for u in raw_urls[:5]:
        if not isinstance(u, str) or not u.startswith(("http://", "https://")):
            continue
        if not is_safe_public_http_url(u):
            continue
        urls.append(u)
    if not urls:
        return {"ok": False, "error": "No safe public URLs given"}

    payload = {
        "urls": urls,
        "extract_depth": "advanced" if (args or {}).get("deep") else "basic",
        "format": "markdown",
        "include_images": False,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        from services.retry_guard import get_breaker
        _br = get_breaker("tavily")
        if not _br.allow():
            return {"ok": False, "error": "URL fetch temporarily unavailable "
                    f"(tavily circuit open — retry in ~{_br.retry_after_s():.0f}s)"}
        # Phase 3 · Custom-breaker reconciliation (2026-02-12) — migrated to
        # ext_client("tavily", ...) for uniform header injection + safety.
        # Same rationale as web_search above; the explicit
        # get_breaker("tavily") wrapper is preserved because ext_client's
        # CM form does not auto-apply the breaker.
        async with ext_client("tavily", timeout=httpx.Timeout(TAVILY_TIMEOUT)) as c:
            r = await c.post(f"{TAVILY_BASE}/extract", json=payload, headers=headers)
    except httpx.TimeoutException:
        _br.record_failure("timeout")
        return {"ok": False, "error": "Tavily extract timed out after 15s"}
    except httpx.RequestError as e:
        _br.record_failure(repr(e))
        return {"ok": False, "error": f"Tavily extract network error: {e}"}

    if r.status_code == 429:
        return {"ok": False, "error": "Tavily rate-limited (429)"}
    if r.status_code >= 400:
        if r.status_code >= 500:
            _br.record_failure(f"tavily_status_{r.status_code}")
        return {"ok": False, "error": f"Tavily {r.status_code}: {r.text[:300]}"}
    _br.record_success()

    data, _jerr = _safe_json(r, "Tavily")
    if _jerr:
        return _jerr
    out = []
    _rows = data.get("results") or []
    if not isinstance(_rows, list):
        _rows = []
    for row in _rows:
        content = (row.get("raw_content") or row.get("content") or "")
        out.append({
            "url":   row.get("url"),
            "title": row.get("title") or "",
            "content": content[:8000],   # hard cap so the LLM context is safe
            "truncated": len(content) > 8000,
        })
    failed = data.get("failed_results") or []
    if not isinstance(failed, list):
        failed = []
    return {"ok": True, "results": out, "failed": failed, "count": len(out)}


# ── 3. web_search_and_summarize (Tavily /search w/ include_answer) ────
async def web_search_and_summarize(ctx: dict, args: dict) -> dict:
    """Search the web AND get Tavily's own 1-paragraph answer."""
    key = _tavily_key()
    if not key:
        return {"ok": False, "error": "TAVILY_API_KEY not configured on server"}
    query = _safe_str((args or {}).get("query"))
    if not query:
        return {"ok": False, "error": "Missing required arg `query`"}
    max_results = _safe_int((args or {}).get("max_results"), 5, 1, 8)

    payload = {
        "query": query[:380],
        "search_depth": "advanced" if (args or {}).get("deep") else "basic",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
        "include_images": False,
        "topic": (args or {}).get("topic") or "general",
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        # 2026-11-25 audit Part IX D(c): this Tavily call had NO
        # circuit breaker (only web_search/fetch_url had one).
        from services.retry_guard import get_breaker
        _br = get_breaker("tavily")
        if not _br.allow():
            return {"ok": False, "error": "Web search temporarily unavailable "
                    f"(tavily circuit open — retry in ~{_br.retry_after_s():.0f}s)"}
        async with ext_client(
            "tavily",
            timeout=httpx.Timeout(connect=5.0, read=TAVILY_TIMEOUT, write=5.0, pool=5.0),
        ) as c:
            r = await c.post(f"{TAVILY_BASE}/search", json=payload, headers=headers)
    except httpx.TimeoutException:
        _br.record_failure("timeout")
        return {"ok": False, "error": "Tavily summarize timed out after 15s"}
    except httpx.RequestError as e:
        _br.record_failure(repr(e))
        return {"ok": False, "error": f"Tavily summarize network error: {e}"}

    if r.status_code == 429:
        return {"ok": False, "error": "Tavily rate-limited (429)"}
    if r.status_code >= 400:
        if r.status_code >= 500:
            _br.record_failure(f"tavily_status_{r.status_code}")
        return {"ok": False, "error": f"Tavily {r.status_code}: {r.text[:300]}"}
    _br.record_success()

    data, _jerr = _safe_json(r, "Tavily")
    if _jerr:
        return _jerr
    _rows = data.get("results") or []
    if not isinstance(_rows, list):
        _rows = []
    citations = [{
        "title": row.get("title") or "",
        "url":   row.get("url") or "",
        "snippet": (row.get("content") or "")[:300],
    } for row in _rows[:max_results] if isinstance(row, dict)]

    return {
        "ok": True,
        "query": query,
        "answer": (data.get("answer") or "").strip(),
        "citations": citations,
        "count": len(citations),
    }


# ── 4. firecrawl_scrape (Firecrawl /v1/scrape) ────────────────────────
async def firecrawl_scrape(ctx: dict, args: dict) -> dict:
    """JS-rendered scrape of a single URL (Firecrawl)."""
    key = _firecrawl_key()
    if not key:
        return {"ok": False,
                "error": "FIRECRAWL_API_KEY not configured on server. "
                         "Add it to backend/.env to enable JS-heavy scraping."}
    url = _safe_str((args or {}).get("url"))
    if not url or not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Missing or invalid `url`"}
    if not is_safe_public_http_url(url):
        return {"ok": False, "error": "URL refused by SSRF guard"}

    formats = (args or {}).get("formats") or ["markdown"]
    if isinstance(formats, str):
        formats = [formats]
    elif not isinstance(formats, list):
        formats = ["markdown"]
    formats = [f for f in formats if f in ("markdown", "html", "screenshot", "links")][:3]
    if not formats:
        formats = ["markdown"]

    payload: dict[str, Any] = {"url": url, "formats": formats}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        # 2026-11-25 audit Part IX D(c): Firecrawl had NO circuit
        # breaker (only Tavily did). Mirror the Tavily pattern.
        from services.retry_guard import get_breaker
        _br = get_breaker("firecrawl")
        if not _br.allow():
            return {"ok": False, "error": "Firecrawl scrape temporarily unavailable "
                    f"(firecrawl circuit open — retry in ~{_br.retry_after_s():.0f}s)"}
        async with ext_client(
            "firecrawl",
            timeout=httpx.Timeout(connect=5.0, read=FIRECRAWL_SCRAPE_TIMEOUT, write=5.0, pool=5.0),
        ) as c:
            r = await c.post(f"{FIRECRAWL_BASE}/scrape", json=payload, headers=headers)
    except httpx.TimeoutException:
        _br.record_failure("timeout")
        return {"ok": False, "error": "Firecrawl scrape timed out after 60s"}
    except httpx.RequestError as e:
        _br.record_failure(repr(e))
        return {"ok": False, "error": f"Firecrawl scrape network error: {e}"}

    if r.status_code == 429:
        return {"ok": False, "error": "Firecrawl rate-limited (429)"}
    if r.status_code >= 400:
        if r.status_code >= 500:
            _br.record_failure(f"firecrawl_status_{r.status_code}")
        return {"ok": False, "error": f"Firecrawl {r.status_code}: {r.text[:300]}"}
    _br.record_success()

    payload_out, _jerr = _safe_json(r, "Firecrawl")
    if _jerr:
        return _jerr
    data = payload_out.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    md = (data.get("markdown") or "")
    return {
        "ok": bool(payload_out.get("success", True)),
        "url": url,
        "title": (data.get("metadata") or {}).get("title") or "",
        "markdown": md[:12_000],
        "truncated": len(md) > 12_000,
        "screenshot_url": data.get("screenshot"),
        "links": (data.get("links") or [])[:50],
    }


# ── 5. firecrawl_crawl_site (Firecrawl /v1/crawl async) ───────────────
async def firecrawl_crawl_site(ctx: dict, args: dict) -> dict:
    """Crawl up to N pages from a domain (Firecrawl)."""
    key = _firecrawl_key()
    if not key:
        return {"ok": False,
                "error": "FIRECRAWL_API_KEY not configured on server. "
                         "Add it to backend/.env to enable site crawling."}
    url = _safe_str((args or {}).get("url"))
    if not url or not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Missing or invalid `url`"}
    if not is_safe_public_http_url(url):
        return {"ok": False, "error": "URL refused by SSRF guard"}

    limit = _safe_int((args or {}).get("limit"), 10, 1, 50)

    payload = {
        "url": url,
        "limit": limit,
        "scrapeOptions": {"formats": ["markdown"]},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    # 2026-11-25 audit Part IX D(e): the poll budget is a WALL-CLOCK
    # deadline covering kickoff + every poll request + sleeps. The
    # old code counted only sleeps, so wall time could reach ~360s
    # against an advertised 90s cap.
    _poll_started = time.monotonic()
    _poll_deadline = _poll_started + FIRECRAWL_CRAWL_POLL_MAX
    try:
        # 2026-11-25 audit Part IX D(c): Firecrawl had NO circuit
        # breaker. Mirror the Tavily pattern; the kickoff gate covers
        # the poll loop below (it shares _br).
        from services.retry_guard import get_breaker
        _br = get_breaker("firecrawl")
        if not _br.allow():
            return {"ok": False, "error": "Firecrawl crawl temporarily unavailable "
                    f"(firecrawl circuit open — retry in ~{_br.retry_after_s():.0f}s)"}
        async with ext_client(
            "firecrawl",
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        ) as c:
            r = await c.post(
                f"{FIRECRAWL_BASE}/crawl", json=payload, headers=headers,
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=max(1.0, _poll_deadline - time.monotonic()),
                    write=5.0, pool=5.0),
            )
    except httpx.RequestError as e:
        _br.record_failure(repr(e))
        return {"ok": False, "error": f"Firecrawl crawl kickoff failed: {e}"}

    if r.status_code >= 400:
        if r.status_code >= 500:
            _br.record_failure(f"firecrawl_status_{r.status_code}")
        return {"ok": False, "error": f"Firecrawl {r.status_code}: {r.text[:300]}"}
    _br.record_success()

    job, _jerr = _safe_json(r, "Firecrawl")
    if _jerr:
        return _jerr
    job_id = job.get("id") or job.get("jobId")
    if not job_id:
        return {"ok": False, "error": f"Firecrawl crawl returned no id: {job}"}

    # Poll until the wall-clock deadline (not a sleep counter).
    delay = 2.0
    async with ext_client(
        "firecrawl",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as c:
        while time.monotonic() < _poll_deadline:
            try:
                pr = await c.get(
                    f"{FIRECRAWL_BASE}/crawl/{job_id}",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=httpx.Timeout(
                        connect=5.0,
                        read=max(1.0, _poll_deadline - time.monotonic()),
                        write=5.0, pool=5.0),
                )
            except httpx.RequestError as e:
                _br.record_failure(repr(e))
                return {"ok": False, "error": f"Firecrawl poll network error: {e}",
                        "job_id": job_id}
            if pr.status_code >= 400:
                if pr.status_code >= 500:
                    _br.record_failure(f"firecrawl_poll_{pr.status_code}")
                return {"ok": False,
                        "error": f"Firecrawl poll {pr.status_code}: {pr.text[:300]}",
                        "job_id": job_id}
            body, _jerr = _safe_json(pr, "Firecrawl")
            if _jerr:
                return {"ok": False, "error": _jerr["error"], "job_id": job_id}
            status = body.get("status")
            if status == "completed":
                _br.record_success()
                pages = []
                _items = body.get("data") or []
                if not isinstance(_items, list):
                    _items = []
                for item in _items[:limit]:
                    md = (item.get("markdown") or "")
                    pages.append({
                        "url":   (item.get("metadata") or {}).get("sourceURL")
                                 or item.get("url"),
                        "title": (item.get("metadata") or {}).get("title") or "",
                        "markdown": md[:4_000],
                        "truncated": len(md) > 4_000,
                    })
                return {"ok": True, "job_id": job_id,
                        "pages": pages, "count": len(pages)}
            if status in ("failed", "cancelled"):
                return {"ok": False, "job_id": job_id,
                        "error": f"Firecrawl crawl {status}"}
            _sleep_left = _poll_deadline - time.monotonic()
            if _sleep_left <= 0:
                break
            await asyncio.sleep(min(delay, _sleep_left))
            delay = min(delay * 1.5, 6.0)

    _elapsed = time.monotonic() - _poll_started
    return {"ok": False, "job_id": job_id,
            "error": (f"Firecrawl crawl still running after {_elapsed:.0f}s "
                      f"(budget {FIRECRAWL_CRAWL_POLL_MAX:.0f}s)")}


# ── Public dispatch tables ────────────────────────────────────────────
WEB_TOOLS = {
    "web_search":                 web_search,
    "fetch_url":                  fetch_url,
    "web_search_and_summarize":   web_search_and_summarize,
    "firecrawl_scrape":           firecrawl_scrape,
    "firecrawl_crawl_site":       firecrawl_crawl_site,
}


WEB_TOOL_SPECS = [
    {
        "name": "web_search",
        "description": (
            "Live Google-style web search via Tavily. Use whenever you need "
            "FRESH facts the model doesn't know — current docs, latest release, "
            "library version, news. Returns top results with titles, URLs and "
            "snippets. Cite the URLs in your answer."
        ),
        "args_spec": {
            "query":       "string — natural-language query, <=380 chars",
            "max_results": "optional int 1–10, default 5",
            "topic":       "optional 'general'|'news', default 'general'",
            "deep":        "optional bool — set true for 'advanced' depth (2x cost)",
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Read the clean text of a public URL (or up to 5). Use when the "
            "user pastes a link, or when you have a URL from web_search and "
            "need its content. Returns markdown."
        ),
        "args_spec": {
            "url":  "string — single URL (http/https only)",
            "urls": "OR array of up to 5 URLs",
            "deep": "optional bool — 'advanced' depth (better tables/PDF)",
        },
    },
    {
        "name": "web_search_and_summarize",
        "description": (
            "Search the web AND get Tavily's own 1-paragraph answer across "
            "the result set. Use for quick fact questions where you'd "
            "otherwise need to web_search + fetch_url + read each result."
        ),
        "args_spec": {
            "query":       "string — natural-language query",
            "max_results": "optional int 1–8, default 5",
            "deep":        "optional bool — advanced depth",
        },
    },
    {
        "name": "firecrawl_scrape",
        "description": (
            "JS-rendered scrape of a single URL via Firecrawl. Use ONLY when "
            "fetch_url returns empty/garbage on a JS-heavy page (Twitter, "
            "single-page apps, dashboards). Slower + costlier than fetch_url."
        ),
        "args_spec": {
            "url":     "string — http/https URL",
            "formats": "optional ['markdown'|'html'|'screenshot'|'links']",
        },
    },
    {
        "name": "firecrawl_crawl_site",
        "description": (
            "Crawl up to N pages from a domain (default 10, max 50). Use for "
            "competitor blogs, doc sites — when one URL isn't enough."
        ),
        "args_spec": {
            "url":   "string — starting URL",
            "limit": "optional int 1–50, default 10",
        },
    },
]


async def invoke_web_tool(name: str, args: dict, ctx: dict) -> Optional[dict]:
    fn = WEB_TOOLS.get(name)
    if not fn:
        return None
    try:
        return await fn(ctx, args or {})
    except Exception as e:
        logger.exception(f"web tool {name} crashed")
        return {"ok": False, "error": f"{name} crashed: {e}"}
