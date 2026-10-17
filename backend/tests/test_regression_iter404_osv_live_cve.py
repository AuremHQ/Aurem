"""tests/test_regression_iter404_osv_live_cve.py — Ticket B (2026-02).

Live CVE lookup via OSV.dev in codebase_health_core.py, replacing the
hardcoded 7-entry `_CVE_DB` as the primary source while keeping it as
the fallback. See CHANGELOG.md / the Ticket B plan for full context.

Covers every scenario called out in the ticket:
  - live result overrides the static map (incl. live-confirmed-clean)
  - OSV failure (timeout / 5xx / breaker-open) falls back to the
    static map — scan result never worse than before this ticket
  - the scanner itself stays synchronous and does ZERO network I/O
  - privacy: the OSV request body contains ONLY package/ecosystem/version
  - bounded fan-out: max 50 new vuln-id lookups, Semaphore(8)
  - combined finding caps at 5 vulns, highest severity first
  - requirements.txt range pins skip live OSV, use static fallback
  - package.json range versions get a "declared range" note
  - unresolvable versions surface as an info finding, not silently dropped
  - ONE real call to api.osv.dev for requests==2.28.0
"""
from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, patch

import pytest

from services import codebase_health_core as chc
from services import osv_client
from services.http.client import ExternalCallError
from services.retry_guard import KNOWN_DEPS


@pytest.fixture(autouse=True)
def _clean_osv_cache():
    """Every test starts from an empty in-process cache so tests can't
    leak state into each other (the cache is a module-level dict by
    design — see codebase_health_core.py's own comment on why)."""
    chc._OSV_CACHE.clear()
    yield
    chc._OSV_CACHE.clear()


# ── 1. Live result overrides the static map ────────────────────────
@pytest.mark.asyncio
async def test_live_result_overrides_static_map():
    text_cache = {"requirements.txt": "requests==2.28.1\n"}
    mocked = {"pypi:requests:2.28.1": [
        {"id": "GHSA-aaaa", "severity": "high", "summary": "x"},
        {"id": "GHSA-bbbb", "severity": "critical", "summary": "y"},
    ]}
    with patch.object(osv_client, "query_osv_batch", AsyncMock(return_value=mocked)), \
         patch.object(chc, "_persist_cache", AsyncMock(return_value=None)):
        await chc.resolve_live_cves(text_cache)

    findings = chc._scan_dependencies(text_cache)
    assert len(findings) == 1
    f = findings[0]
    assert f["id"] == "dep::requirements.txt:1:requests:osv"
    assert f["severity"] == "critical"          # worst of the two
    assert "GHSA-bbbb" in f["message"] and "GHSA-aaaa" in f["message"]


# ── 2. Live-confirmed-clean (empty list) is authoritative ──────────
@pytest.mark.asyncio
async def test_live_empty_result_is_authoritative_even_if_static_map_would_flag_it():
    # requests==2.28.0 IS in the static _CVE_DB (prefix "2.28.", CVE-2023-32681).
    assert any(p == "2.28." for p, _, _ in chc._CVE_DB["requests"])
    text_cache = {"requirements.txt": "requests==2.28.0\n"}
    with patch.object(osv_client, "query_osv_batch", AsyncMock(return_value={"pypi:requests:2.28.0": []})), \
         patch.object(chc, "_persist_cache", AsyncMock(return_value=None)):
        await chc.resolve_live_cves(text_cache)

    findings = chc._scan_dependencies(text_cache)
    assert findings == []  # live says clean -> trust it, don't fall back to the static map


# ── 3. OSV failure modes all fall back to static, never worse ──────
@pytest.mark.parametrize("exc", [
    ExternalCallError("osv", "timed out", status=None, method="POST", url=osv_client.OSV_BATCH_URL),
    ExternalCallError("osv", "POST ... failed with 503 after 2 retries", status=503, method="POST", url=osv_client.OSV_BATCH_URL),
    ExternalCallError("osv", "circuit breaker OPEN for osv — upstream is currently unhealthy", method="POST", url=osv_client.OSV_BATCH_URL),
])
@pytest.mark.asyncio
async def test_osv_failure_falls_back_to_static_cve_db(exc):
    text_cache = {"requirements.txt": "requests==2.28.0\n"}  # matches static prefix "2.28."
    with patch.object(osv_client, "query_osv_batch", AsyncMock(side_effect=exc)):
        await chc.resolve_live_cves(text_cache)  # must NOT raise

    findings = chc._scan_dependencies(text_cache)
    assert len(findings) == 1
    assert findings[0]["id"] == "dep::requirements.txt:1:requests"   # static-path id shape
    assert findings[0]["severity"] == "high"                        # from _CVE_DB's CVE-2023-32681/HIGH

    # And compare directly against the pre-ticket baseline: scanning
    # with NO live cache at all (OSV never even attempted) produces
    # the exact same findings — i.e. a total OSV outage is provably
    # never worse than before this ticket existed.
    chc._OSV_CACHE.clear()
    baseline = chc._scan_dependencies(text_cache)
    assert baseline == findings


# ── 4. Scanner stays sync and does ZERO network I/O ─────────────────
def test_scan_dependencies_is_sync_and_makes_no_network_calls():
    import inspect
    assert not inspect.iscoroutinefunction(chc._scan_dependencies)
    assert not inspect.iscoroutinefunction(chc.SCANNERS["dependencies"])

    text_cache = {
        "requirements.txt": "requests==2.28.0\naxios>=1.0\n",
        "package.json": '{"dependencies": {"axios": "^0.21.0"}}',
    }
    # Block ALL real sockets for the duration of this call — if
    # _scan_dependencies (or anything it calls) attempts network I/O,
    # this raises instead of silently succeeding.
    with patch.object(socket.socket, "connect", side_effect=AssertionError(
        "network I/O attempted from a function that must stay offline"
    )):
        findings = chc._scan_dependencies(text_cache)
    assert isinstance(findings, list)  # ran to completion, no network needed


# ── 5. Privacy: OSV request body has ONLY package/ecosystem/version ─
@pytest.mark.asyncio
async def test_osv_request_body_contains_only_package_ecosystem_version():
    captured = {}

    async def _fake_ext_request(dep, method, url, **kwargs):
        if url == osv_client.OSV_BATCH_URL:
            captured["payload"] = kwargs.get("json")
        class _Resp:
            def json(self_inner):
                return {"results": [{"vulns": []}]}
        return _Resp()

    with patch.object(osv_client, "ext_request", _fake_ext_request):
        await osv_client.query_osv_batch([("PyPI", "requests", "2.28.0")])

    payload = captured["payload"]
    assert set(payload.keys()) == {"queries"}
    assert len(payload["queries"]) == 1
    q = payload["queries"][0]
    assert set(q.keys()) == {"package", "version"}
    assert set(q["package"].keys()) == {"name", "ecosystem"}
    assert q["package"] == {"name": "requests", "ecosystem": "PyPI"}
    assert q["version"] == "2.28.0"
    # No repo name/URL, file content, user info, or tokens anywhere in the payload.
    flat = str(payload).lower()
    for forbidden in ("token", "github", "repo", "password", "secret", "authorization"):
        assert forbidden not in flat


# ── 6. Bounded fan-out: cap 50 distinct ids, Semaphore(8) ───────────
@pytest.mark.asyncio
async def test_bounded_fanout_caps_at_50_distinct_vuln_ids():
    # 60 distinct ids across one package's abbreviated result.
    many_ids = [{"id": f"GHSA-{i:04d}"} for i in range(60)]

    async def _fake_ext_request(dep, method, url, **kwargs):
        class _Resp:
            def json(self_inner):
                if url == osv_client.OSV_BATCH_URL:
                    return {"results": [{"vulns": many_ids}]}
                return {"database_specific": {"severity": "LOW"}, "summary": ""}
        return _Resp()

    detail_calls = []
    orig = osv_client._fetch_vuln_detail

    async def _counting_fetch(vuln_id, sem):
        detail_calls.append(vuln_id)
        return await orig(vuln_id, sem)

    with patch.object(osv_client, "ext_request", _fake_ext_request), \
         patch.object(osv_client, "_fetch_vuln_detail", _counting_fetch):
        result = await osv_client.query_osv_batch([("PyPI", "pkg", "1.0.0")])

    assert len(detail_calls) == osv_client.MAX_NEW_VULN_LOOKUPS == 50
    # But ALL 60 real vuln ids still show up in the result (capped
    # detail-fetch only affects severity/summary richness, never drops
    # a genuine vulnerability).
    assert len(result["pypi:pkg:1.0.0"]) == 60


@pytest.mark.asyncio
async def test_bounded_fanout_concurrency_never_exceeds_8():
    in_flight = {"current": 0, "max_seen": 0}

    async def _slow_fetch(vuln_id, sem):
        async with sem:
            in_flight["current"] += 1
            in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
            await asyncio.sleep(0.01)
            in_flight["current"] -= 1
            return {"id": vuln_id, "severity": None, "summary": ""}

    many_ids = [{"id": f"GHSA-{i:04d}"} for i in range(20)]

    async def _fake_ext_request(dep, method, url, **kwargs):
        class _Resp:
            def json(self_inner):
                return {"results": [{"vulns": many_ids}]}
        return _Resp()

    with patch.object(osv_client, "ext_request", _fake_ext_request), \
         patch.object(osv_client, "_fetch_vuln_detail", _slow_fetch):
        await osv_client.query_osv_batch([("PyPI", "pkg", "1.0.0")])

    assert in_flight["max_seen"] <= osv_client.VULN_LOOKUP_CONCURRENCY == 8


# ── 7. Combined finding caps at 5 vulns, highest severity first ────
def test_combined_finding_caps_at_5_highest_severity_first():
    vulns = [
        {"id": "GHSA-low1", "severity": "low"},
        {"id": "GHSA-crit1", "severity": "critical"},
        {"id": "GHSA-med1", "severity": "medium"},
        {"id": "GHSA-high1", "severity": "high"},
        {"id": "GHSA-crit2", "severity": "critical"},
        {"id": "GHSA-low2", "severity": "low"},
        {"id": "GHSA-info1", "severity": "info"},
    ]
    f = chc._dep_osv_finding("requirements.txt", 1, "pkg", "1.0.0", vulns, None)
    assert f["severity"] == "critical"
    assert "(+2 more)" in f["message"]
    # exactly 5 ids listed, critical-first
    listed = f["message"].split("→")[1].split("(+")[0].strip().rstrip(" —").split(", ")
    assert listed[0] in ("GHSA-crit1", "GHSA-crit2")
    assert len(listed) == 5


# ── 8. requirements.txt range pin skips live OSV, static fallback ──
@pytest.mark.asyncio
async def test_requirements_txt_range_pin_excluded_from_live_query():
    resolved, _ = chc._extract_declared_packages({"requirements.txt": "fastapi>=0.68.0\n"})
    assert resolved[0]["exact_pin"] is False

    called_with = {"invoked": False}
    async def _spy(pkgs):
        called_with["invoked"] = True
        called_with["pkgs"] = pkgs
        return {}
    with patch.object(osv_client, "query_osv_batch", _spy):
        await chc.resolve_live_cves({"requirements.txt": "fastapi>=0.68.0\n"})
    # resolve_live_cves short-circuits before calling OSV at all when
    # there's nothing eligible to query — even better than a wasted
    # empty-list call.
    assert called_with["invoked"] is False


# ── 9. package.json range version gets a "declared range" note ────
def test_package_json_range_version_gets_range_note():
    resolved, unresolved = chc._extract_declared_packages(
        {"package.json": '{"dependencies": {"axios": "^0.21.0", "left-pad": "1.3.0"}}'}
    )
    by_pkg = {r["pkg"]: r for r in resolved}
    assert by_pkg["axios"]["range_note"] == "declared range, base version checked"
    assert by_pkg["axios"]["ver"] == "0.21.0"
    assert by_pkg["left-pad"]["range_note"] is None  # already an exact version, no prefix stripped
    assert unresolved == []


# ── 10. Unresolvable version -> info finding, not silently dropped ─
def test_unresolvable_version_surfaces_info_finding():
    text_cache = {
        "requirements.txt": "requests\n",
        "package.json": '{"dependencies": {"left-pad": "workspace:*"}}',
    }
    findings = chc._scan_dependencies(text_cache)
    ids = {f["id"] for f in findings}
    assert "dep::requirements.txt:1:requests:unresolved" in ids
    assert "dep::package.json:0:left-pad:unresolved" in ids
    for f in findings:
        assert f["severity"] == "info"  # zero score impact (SEV_WEIGHTS["info"] == 0)
        assert f["fix_tokens"] == 0


# ── 11. osv registered in KNOWN_DEPS (Guard 17), Guard 18 unaffected ─
def test_osv_dep_registered_in_known_deps():
    assert "osv" in KNOWN_DEPS


def test_guard18_timeout_audit_still_green():
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "scripts/timeout_audit.py"],
        cwd="/app/backend", capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "PASS" in r.stdout


# ── 12. ONE real call to api.osv.dev for requests==2.28.0 ──────────
@pytest.mark.asyncio
async def test_real_osv_dev_call_requests_2_28_0():
    try:
        result = await osv_client.query_osv_batch([("PyPI", "requests", "2.28.0")])
    except ExternalCallError as e:
        pytest.skip(f"api.osv.dev unreachable in this environment: {e}")

    vulns = result.get("pypi:requests:2.28.0", [])
    assert isinstance(vulns, list)
    assert len(vulns) > 0  # requests 2.28.0 has multiple known, real advisories
    assert any(v["id"].startswith("GHSA-") for v in vulns)
    for v in vulns:
        assert "id" in v and "severity" in v and "summary" in v
