"""services/onboarding_scan.py — website-scan engines for onboarding.

The two scan engines behind POST /onboarding/scan-website: Firecrawl
(first choice) and a plain-HTTP fallback with our own static checks
(never comes back empty-handed). They used to live in
routers/onboarding.py, which put raw httpx calls in a router — an
architecture-health boundary violation (HTTP belongs in services/).
Moved verbatim 2026-11-20 (health-score architecture fix); the router
now only orchestrates: auth, URL normalisation, funnel events, sorting.

Both engines depend only on services (web_skills for the Firecrawl key,
url_guard for the SSRF allow-list) plus stdlib/third-party HTTP+HTML.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException

from services.url_guard import is_safe_public_http_url
from services.web_skills import FIRECRAWL_BASE, _firecrawl_key

logger = logging.getLogger(__name__)


async def _scan_via_firecrawl(url: str) -> Optional[dict]:
    key = _firecrawl_key()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)) as c:
            r = await c.post(
                f"{FIRECRAWL_BASE}/scrape",
                json={"url": url, "formats": ["markdown"]},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
    except (httpx.TimeoutException, httpx.RequestError) as e:
        logger.info("[onboarding-scan] firecrawl network error, falling back: %r", e)
        return None
    if r.status_code >= 400:
        logger.info("[onboarding-scan] firecrawl %s, falling back: %s", r.status_code, r.text[:200])
        return None
    body = r.json() or {}
    data = body.get("data") or {}
    if not body.get("success", True) or not data.get("markdown"):
        return None
    title = (data.get("metadata") or {}).get("title") or ""
    md = data.get("markdown") or ""
    issues = []
    if not title:
        issues.append({"severity": "medium", "title": "No page title found",
                        "detail": "Search engines and browser tabs show a blank title."})
    if len(md.strip()) < 200:
        issues.append({"severity": "high", "title": "Almost no visible content",
                        "detail": "Very little readable text was found on this page."})
    return {"scan_path": "firecrawl", "title": title, "issues": issues}



async def _scan_via_fallback(url: str) -> dict:
    """Plain HTTP fetch + our own static checks — runs whenever
    Firecrawl is unavailable (exhausted credits, down, no key), so a
    website scan NEVER comes back empty-handed."""
    if not is_safe_public_http_url(url):
        raise HTTPException(400, "That URL isn't allowed (internal/private address)")
    issues = []
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=5.0),
                                      follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "ORA-Scanner/1.0 (+https://auremcto.com)"})
    except httpx.TimeoutException:
        raise HTTPException(502, "That site took too long to respond (12s timeout)")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Couldn't reach that site: {e}")
    elapsed_ms = round((time.monotonic() - t0) * 1000)

    if r.status_code >= 400:
        issues.append({"severity": "high", "title": f"Site returned HTTP {r.status_code}",
                        "detail": "Visitors (and search engines) will see an error page."})
    if not url.startswith("https://"):
        issues.append({"severity": "high", "title": "Not using HTTPS",
                        "detail": "Browsers flag non-HTTPS sites as \"Not secure\"."})
    if elapsed_ms > 3000:
        issues.append({"severity": "medium", "title": f"Slow response ({elapsed_ms}ms)",
                        "detail": "Pages over ~3s to respond lose visitors and rank lower in search."})

    soup = BeautifulSoup(r.text or "", "html.parser")
    title_tag = soup.find("title")
    if not title_tag or not (title_tag.text or "").strip():
        issues.append({"severity": "medium", "title": "No page title found",
                        "detail": "Search engines and browser tabs show a blank title."})
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc or not (meta_desc.get("content") or "").strip():
        issues.append({"severity": "low", "title": "No meta description",
                        "detail": "Search results show a generic/blank snippet without one."})
    if not soup.find("meta", attrs={"name": "viewport"}):
        issues.append({"severity": "medium", "title": "No mobile viewport tag",
                        "detail": "The page may not render correctly on phones."})
    if not soup.find("link", rel=lambda v: v and "icon" in v.lower()):
        issues.append({"severity": "low", "title": "No favicon",
                        "detail": "The site has no icon for browser tabs/bookmarks."})

    links = list({a.get("href") for a in soup.find_all("a", href=True)
                  if a.get("href", "").startswith(("http://", "https://"))})[:8]
    broken = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=4.0, write=3.0, pool=3.0)) as c:
        for link in links:
            if not is_safe_public_http_url(link):
                continue
            try:
                lr = await c.head(link, follow_redirects=True)
                if lr.status_code >= 400:
                    broken += 1
            except httpx.RequestError:
                broken += 1
    if broken > 0:
        issues.append({"severity": "medium", "title": f"{broken} broken link(s) found",
                        "detail": f"Checked {len(links)} on-page links; {broken} failed to load."})

    return {"scan_path": "fallback", "title": (title_tag.text or "").strip() if title_tag else "",
            "issues": issues, "response_time_ms": elapsed_ms, "http_status": r.status_code}

