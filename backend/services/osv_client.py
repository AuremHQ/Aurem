"""services/osv_client.py — Ticket B (2026-02), live CVE lookup via OSV.dev.

Isolated, mockable HTTP layer for the free/keyless OSV.dev vulnerability
database (https://osv.dev). Two-stage API, per OSV's own docs:

  1. POST /v1/querybatch — cheap, abbreviated: returns vuln IDs only.
  2. GET  /v1/vulns/{id} — one call per DISTINCT new vuln id, for the
     severity/summary detail `querybatch` omits.

Privacy: the ONLY data sent to OSV is {package name, ecosystem, version}.
Never the repo name/URL, file content, user info, or tokens — callers in
`codebase_health_core.py` build exactly that tuple and nothing else.

Both stages go through `services/http/client.py::ext_request`, which
already wraps every outbound call with Guard 17 (retry_guard circuit
breaker, dep="osv") and Guard 18 (httpx timeout kwarg) — no new
retry/timeout code needed here, per AGENTS.md "reuse over reinvent".

Bounded fan-out: stage 2 only fetches details for the first
`MAX_NEW_VULN_LOOKUPS` distinct ids (capped at `VULN_LOOKUP_CONCURRENCY`
concurrent requests). Vulns beyond the cap are still reported (the id is
real), just without enriched severity/summary — `_norm_sev(None)` falls
back to "medium", the same safe default used everywhere else in this
file's existing `_CVE_DB` fallback path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from services.http import ext_request
from services.http.client import ExternalCallError

logger = logging.getLogger(__name__)

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL_TMPL = "https://api.osv.dev/v1/vulns/{}"

MAX_NEW_VULN_LOOKUPS = 50
VULN_LOOKUP_CONCURRENCY = 8


def _cache_key(ecosystem: str, pkg: str, ver: str) -> str:
    return f"{ecosystem.lower()}:{pkg.lower()}:{ver}"


def _extract_severity(vuln_detail: dict) -> Optional[str]:
    """GHSA-style `database_specific.severity` is a plain bucket label
    ("LOW"/"MODERATE"/"HIGH"/"CRITICAL") — far simpler and more reliable
    than parsing a raw CVSS vector string, and it's what most OSV
    records actually populate. Falls back to None (→ caller's
    `_norm_sev` defaults to "medium") when absent, same as today's
    static-map behaviour for anything not in the curated list."""
    sev = (vuln_detail.get("database_specific") or {}).get("severity")
    if isinstance(sev, str) and sev:
        s = sev.strip().upper()
        return "medium" if s == "MODERATE" else s
    return None


async def _fetch_vuln_detail(vuln_id: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            r = await ext_request("osv", "GET", OSV_VULN_URL_TMPL.format(vuln_id))
            data = r.json() or {}
            return {"id": vuln_id, "severity": _extract_severity(data),
                    "summary": (data.get("summary") or "")[:200]}
        except ExternalCallError as e:
            logger.info("[osv] detail fetch failed for %s (non-fatal): %s", vuln_id, e)
            return {"id": vuln_id, "severity": None, "summary": ""}


async def query_osv_batch(pkgs: list[tuple[str, str, str]]) -> dict[str, list[dict]]:
    """pkgs: list of (ecosystem, package, version), e.g. ("PyPI", "requests", "2.28.0").

    Returns {cache_key: [{"id","severity","summary"}, ...]} — an empty
    list means OSV was reached and genuinely found nothing (a real,
    authoritative "clean" result, distinct from "never checked").

    Raises ExternalCallError on total failure (network error, timeout,
    5xx after retries, or breaker-open — `ext_request` normalizes all
    of these to one exception type) — the caller
    (`codebase_health_core.resolve_live_cves`) catches this and
    leaves the live cache untouched, so `_scan_dependencies` falls back
    to the static `_CVE_DB` exactly as it did before this ticket.
    """
    if not pkgs:
        return {}

    payload = {
        "queries": [
            {"package": {"name": pkg, "ecosystem": eco}, "version": ver}
            for eco, pkg, ver in pkgs
        ]
    }
    r = await ext_request("osv", "POST", OSV_BATCH_URL, json=payload)
    body = r.json() or {}
    raw_results = body.get("results") or []

    # querybatch returns results in the SAME ORDER as the input queries
    # (OSV's own contract) — zip positionally back onto (eco, pkg, ver).
    abbreviated: dict[str, list[str]] = {}
    all_ids: list[str] = []
    for (eco, pkg, ver), result in zip(pkgs, raw_results):
        ids = [v["id"] for v in (result.get("vulns") or []) if v.get("id")]
        abbreviated[_cache_key(eco, pkg, ver)] = ids
        all_ids.extend(ids)

    distinct_ids = list(dict.fromkeys(all_ids))  # de-dupe, preserve order
    to_fetch = distinct_ids[:MAX_NEW_VULN_LOOKUPS]
    if len(distinct_ids) > MAX_NEW_VULN_LOOKUPS:
        logger.info("[osv] %d distinct vuln ids found, capping detail-fetch at %d",
                     len(distinct_ids), MAX_NEW_VULN_LOOKUPS)

    sem = asyncio.Semaphore(VULN_LOOKUP_CONCURRENCY)
    details_list = await asyncio.gather(
        *(_fetch_vuln_detail(vid, sem) for vid in to_fetch)
    )
    details_by_id = {d["id"]: d for d in details_list}

    out: dict[str, list[dict]] = {}
    for key, ids in abbreviated.items():
        out[key] = [
            details_by_id.get(vid, {"id": vid, "severity": None, "summary": ""})
            for vid in ids
        ]
    return out
