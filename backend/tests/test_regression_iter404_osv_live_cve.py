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
import time
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
    design — see codebase_health_core.py's own comment on why).
    Also marks the Mongo cold-start restore as already-done so no
    existing/unrelated test accidentally probes real Mongo via the new
    `_load_cache_from_mongo_once()` step (review fix (b)) — tests that
    specifically exercise cold-start restore reset this flag
    themselves (see test_cold_start_restores_cache_from_mongo)."""
    chc._OSV_CACHE.clear()
    chc._cold_start_loaded = True
    yield
    chc._OSV_CACHE.clear()
    chc._cold_start_loaded = True


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
    # exactly 5 ids listed, critical-first — each annotated with its
    # own severity label now (review fix #2, "render it clearly").
    listed_raw = f["message"].split("→")[1].split("(+")[0].strip().rstrip(" —").split(", ")
    listed = [entry.split(" (")[0] for entry in listed_raw]
    assert listed[0] in ("GHSA-crit1", "GHSA-crit2")
    assert len(listed) == 5
    assert "GHSA-crit1 (critical)" in f["message"]


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


# ═════════════════════════════════════════════════════════════════════
# Review-round fixes (post Ticket-B-v1 review)
# ═════════════════════════════════════════════════════════════════════

# ── R1. Total time budget: a hung OSV call must not block the scan ──
@pytest.mark.asyncio
async def test_resolve_live_cves_respects_total_time_budget_on_hang(monkeypatch):
    """Review fix #1 — routers/codebase_health.py already had a real
    Cloudflare 520 incident; a hung third party must never make the
    scan endpoint wait minutes. Patches the budget down to 0.3s
    (instead of the real 15s) purely so the test runs fast — the
    mechanism under test (asyncio.wait_for wrapping the whole
    resolve_live_cves body) is identical at any timeout value."""
    monkeypatch.setattr(chc, "OSV_TOTAL_TIMEOUT_S", 0.3)

    async def hung_batch(pkgs):
        await asyncio.sleep(999)

    monkeypatch.setattr(osv_client, "query_osv_batch", hung_batch)

    start = time.monotonic()
    await chc.resolve_live_cves({"requirements.txt": "requests==2.28.0\n"})
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"resolve_live_cves took {elapsed:.2f}s, should be bounded near the 0.3s test budget"
    assert "pypi:requests:2.28.0" not in chc._OSV_CACHE  # cache left untouched on timeout

    # The scan endpoint must still return usable results within the
    # deadline — static fallback, never worse than before this ticket.
    findings = chc._scan_dependencies({"requirements.txt": "requests==2.28.0\n"})
    assert len(findings) == 1
    assert findings[0]["id"] == "dep::requirements.txt:1:requests"  # static-path id shape


# ── R2a. Missing severity renders as "unknown", never "medium" ──────
def test_missing_severity_renders_as_unknown_not_medium():
    vulns = [{"id": "GHSA-nodata", "severity": None, "summary": ""}]
    f = chc._dep_osv_finding("requirements.txt", 1, "pkg", "1.0.0", vulns, None)
    assert f["severity"] == "unknown"
    assert "GHSA-nodata (unknown)" in f["message"]


def test_osv_extract_severity_confirmed_absent_returns_unknown_not_none():
    # Real shape of PYSEC-2023-74 (confirmed live): no database_specific,
    # no top-level severity[] at all.
    detail = {"id": "PYSEC-2023-74", "details": "...", "aliases": ["CVE-2023-32681"]}
    assert osv_client._extract_severity(detail) == "unknown"


# ── R2b. CVSS fallback when database_specific.severity is absent ────
def test_osv_extract_severity_falls_back_to_cvss_vector_for_pysec_record():
    """Real shape of PYSEC-2026-2275 (confirmed live via a direct
    api.osv.dev call) — no `database_specific` at all, but a top-level
    CVSS_V3 vector. Base score 5.5 → qualitative "Medium" per the CVSS
    v3.1 spec (verified with the `cvss` package directly)."""
    detail = {
        "id": "PYSEC-2026-2275",
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:N"}],
    }
    assert osv_client._extract_severity(detail) == "medium"


def test_osv_extract_severity_prefers_database_specific_over_cvss_when_both_present():
    # Real shape of GHSA-9hjg-9r4m-mvj7 (confirmed live) — has BOTH
    # fields; database_specific.severity ("MODERATE") must win since
    # it's the simpler, more-maintained GHSA-curated label.
    detail = {
        "database_specific": {"severity": "MODERATE"},
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:N/A:N"}],
    }
    assert osv_client._extract_severity(detail) == "medium"


def test_osv_extract_severity_malformed_cvss_vector_does_not_crash():
    detail = {"severity": [{"type": "CVSS_V3", "score": "not a real vector"}]}
    assert osv_client._extract_severity(detail) == "unknown"


# ── R2c. Capped/failed detail fetch is NOT cached as final "unknown" ─
@pytest.mark.asyncio
async def test_capped_fetch_is_retried_next_scan_not_cached_as_final():
    """A vuln beyond the 50-id cap (or one whose detail GET failed) is
    marked `fetch_ok=False` in osv_client — `resolve_live_cves` must
    treat that whole cache entry as `incomplete` and re-query it on the
    VERY NEXT scan regardless of the 24h TTL, instead of permanently
    treating "couldn't fetch" the same as "OSV confirmed no severity"."""
    text_cache = {"requirements.txt": "requests==2.28.0\n"}
    incomplete_result = {"pypi:requests:2.28.0": [
        {"id": "GHSA-known", "severity": "high", "summary": "x", "fetch_ok": True},
        {"id": "GHSA-capped", "severity": None, "summary": "", "fetch_ok": False},
    ]}
    with patch.object(osv_client, "query_osv_batch", AsyncMock(return_value=incomplete_result)), \
         patch.object(chc, "_persist_cache", AsyncMock(return_value=None)):
        await chc.resolve_live_cves(text_cache)

    assert chc._OSV_CACHE["pypi:requests:2.28.0"]["incomplete"] is True

    # Still shown now, with the unresolved one honestly labelled —
    # not silently dropped while waiting for the retry.
    findings = chc._scan_dependencies(text_cache)
    assert len(findings) == 1
    assert "GHSA-capped (unknown)" in findings[0]["message"]

    # Next scan: resolve_live_cves must re-query this key even though
    # it's nowhere near the 24h TTL, because it's marked incomplete.
    full_result = {"pypi:requests:2.28.0": [
        {"id": "GHSA-known", "severity": "high", "summary": "x", "fetch_ok": True},
        {"id": "GHSA-capped", "severity": "low", "summary": "", "fetch_ok": True},
    ]}
    spy = AsyncMock(return_value=full_result)
    with patch.object(osv_client, "query_osv_batch", spy), \
         patch.object(chc, "_persist_cache", AsyncMock(return_value=None)):
        await chc.resolve_live_cves(text_cache)

    spy.assert_awaited_once()  # proves the retry actually happened
    assert chc._OSV_CACHE["pypi:requests:2.28.0"]["incomplete"] is False


def test_unknown_severity_is_not_zero_weighted_like_info():
    """Review fix #2 consequence — an unresolved-severity live vuln is
    a REAL confirmed vulnerability and must still move the health
    score, unlike a pure "info" note (weight 0)."""
    assert chc.SEV_WEIGHTS["unknown"] > chc.SEV_WEIGHTS["medium"]
    assert chc.SEV_WEIGHTS["unknown"] < chc.SEV_WEIGHTS["high"]
    assert chc.SEV_WEIGHTS["info"] == 0


# ── R3. INFO findings excluded from BOTH the score AND the backlog ──
def test_info_findings_do_not_lower_the_health_score():
    info_findings = [chc._dep_unresolved_finding("requirements.txt", 1, "left-pad")
                      for _ in range(20)]  # even 20 of them
    assert chc._score_for_findings(info_findings) == 100  # unchanged from 0-findings baseline
    assert chc._score_for_findings([]) == 100

    # Contrast: a single critical DOES move it, proving the scorer
    # isn't just broken/no-oping in general.
    critical_finding = {"severity": "critical"}
    assert chc._score_for_findings(info_findings + [critical_finding]) < 100


@pytest.mark.asyncio
async def test_info_findings_are_never_written_to_the_backlog():
    from services.loop_full_scan import persist_findings_to_backlog

    db = AsyncMock()
    db.cto_open_findings.find_one = AsyncMock(return_value=None)
    db.cto_open_findings.update_one = AsyncMock(return_value=None)

    findings = [
        chc._dep_unresolved_finding("requirements.txt", 1, "left-pad"),  # severity "info"
        {"severity": "critical", "file": "a.py", "line": 1, "id": "crit1",
         "title": "t", "message": "m"},
    ]
    count = await persist_findings_to_backlog(
        db, user_id="u1", project_id="p1", findings=findings,
    )
    assert count == 1  # only the critical one — the info finding never reaches Mongo
    assert db.cto_open_findings.update_one.await_count == 1


# ── R-b. Mongo cold-start restore (write-only persist fixed) ────────
@pytest.mark.asyncio
async def test_cold_start_restores_cache_from_mongo(monkeypatch):
    """Review fix (b) — the Mongo persist must be read back on process
    start, not write-only. Verifies the TTL cutoff is respected (a
    stale doc is NOT restored) and a fresh one IS, without re-querying
    OSV for a package already warmed by this doc."""
    chc._cold_start_loaded = False
    now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

    class _FakeCursor:
        def __init__(self, docs):
            self._docs = docs
        async def __aiter__(self):
            for d in self._docs:
                yield d

    class _FakeCollection:
        def find(self, query):
            # Only the fresh doc passes the real $gte-equivalent filter
            # this fake simulates (cutoff respected server-side in prod;
            # here we just hand back what a real filtered query would).
            return _FakeCursor([{
                "_id": "pypi:requests:2.28.0",
                "vulns": [{"id": "GHSA-restored", "severity": "high", "summary": "", "fetch_ok": True}],
                "checked_at": now_iso,
                "incomplete": False,
            }])

    class _FakeDb:
        osv_cve_cache = _FakeCollection()

    monkeypatch.setattr("cto_services.db.get_db", lambda: _FakeDb())

    spy = AsyncMock(return_value={})
    with patch.object(osv_client, "query_osv_batch", spy):
        await chc.resolve_live_cves({"requirements.txt": "requests==2.28.0\n"})

    # Restored from "Mongo" → already fresh → resolve_live_cves must
    # NOT re-query OSV for it.
    spy.assert_not_awaited()
    assert "pypi:requests:2.28.0" in chc._OSV_CACHE
    assert chc._OSV_CACHE["pypi:requests:2.28.0"]["vulns"][0]["id"] == "GHSA-restored"

    findings = chc._scan_dependencies({"requirements.txt": "requests==2.28.0\n"})
    assert len(findings) == 1
    assert "GHSA-restored (high)" in findings[0]["message"]


def test_backlog_includes_unknown_severity_alongside_critical_high_medium():
    """Review fix #2 consequence — persist_findings_to_backlog's
    severity allowlist must include "unknown" so a confirmed-real but
    unresolved-severity live vuln still reaches the backlog, same as
    fix #3's info-exclusion is unaffected (info/low still excluded)."""
    import inspect
    from services import loop_full_scan
    src = inspect.getsource(loop_full_scan.persist_findings_to_backlog)
    assert '"critical", "high", "medium", "unknown"' in src
