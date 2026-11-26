"""tests/test_prompt3_cve_single_source_2026_11_26.py — PROMPT 3 item 4.

Dedup/refresh of the stale CVE maps: ONE canonical source
(services/cve_data.py) imported by both consumers
(codebase_health_core._CVE_DB, bug_hunt_rules._DEP_CVES), refreshed
entries (urllib3 fixed_in 2.0.0 -> 2.0.7; requests health prefixes
extended to 2.29./2.30.), and live OSV PRIMARY with the static map as
fallback only.

Covers:
  (a) both consumers resolve to the SAME canonical data
  (b) a live OSV result wins over the static map when OSV returns data
  (c) OSV timeout / failure / empty result -> static fallback is used
  (d) version comparison still works per the real _vercmp semantics
      (below the pinned/fixed version flags, at-or-above does not)

Live OSV is NEVER hit here — every OSV call is stubbed (the one
pre-existing real-OSV test lives in
test_regression_iter404_osv_live_cve.py and skips when unreachable).
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from services import bug_hunt_rules as bhr
from services import codebase_health_core as chc
from services import cve_data, osv_client
from services.bug_hunt_rules import _vercmp, scan_bug_hunt
from services.http.client import ExternalCallError


@pytest.fixture(autouse=True)
def _clean_osv_cache():
    """Empty cache + cold-start restore already done, so no test here
    ever probes real Mongo (same pattern as the iter404 OSV tests)."""
    chc._OSV_CACHE.clear()
    chc._cold_start_loaded = True
    yield
    chc._OSV_CACHE.clear()
    chc._cold_start_loaded = True


def _cve_titles(findings):
    return {f["title"] for f in findings}


# ── (a) ONE canonical source — both consumers, same data ────────────
def test_both_consumers_import_the_same_canonical_objects():
    # Identity, not just equality: a later edit to one map cannot
    # silently diverge from the other.
    assert chc._CVE_DB is cve_data.CVE_DB
    assert bhr._DEP_CVES is cve_data.DEP_CVES


def test_derived_views_rebuild_identically_from_canonical_entries():
    assert chc._CVE_DB == cve_data.build_cve_db()
    assert bhr._DEP_CVES == cve_data.build_dep_cves()


def test_every_view_entry_traces_to_one_canonical_entry():
    by_key = {(e["package"], e["cve"]): e for e in cve_data.CVE_ENTRIES}
    # No duplicate (package, CVE) in the canonical list itself.
    assert len(by_key) == len(cve_data.CVE_ENTRIES)
    for pkg, entries in cve_data.CVE_DB.items():
        for _prefix, cve, _sev in entries:
            assert (pkg, cve) in by_key
    for pkg, fixed_in, cve, _sev, _msg in cve_data.DEP_CVES:
        assert by_key[(pkg, cve)]["fixed_in"] == fixed_in


def test_no_literal_cve_map_remains_in_either_consumer_file():
    # The maps must not exist as literals in the consumers anymore —
    # only the import from services.cve_data.
    assert "_CVE_DB: dict[str, list[tuple" not in inspect.getsource(chc)
    assert "_DEP_CVES: list[tuple" not in inspect.getsource(bhr)
    assert "from services.cve_data import" in inspect.getsource(chc)
    assert "from services.cve_data import" in inspect.getsource(bhr)


def test_canonical_data_covers_the_union_of_both_old_maps():
    # Old _CVE_DB packages (7) + old _DEP_CVES packages (11) = 14
    # distinct packages; axios carries two CVEs, so 15 entries.
    pkgs = {e["package"] for e in cve_data.CVE_ENTRIES}
    assert pkgs == {
        "requests", "fastapi", "pyjwt", "axios", "lodash", "next", "vite",
        "flask", "django", "pillow", "cryptography", "urllib3", "pyyaml",
        "jinja2",
    }
    assert len(cve_data.CVE_ENTRIES) == 15
    assert len(cve_data.DEP_CVES) == 11  # unchanged count, prefix-only entries excluded
    assert len(cve_data.CVE_DB) == 7     # unchanged count


def test_refreshed_entries_present():
    # urllib3 CVE-2023-43804: fixed_in corrected 2.0.0 -> 2.0.7
    # (2.0.0-2.0.6 are still vulnerable to it).
    urllib3 = [e for e in cve_data.DEP_CVES if e[0] == "urllib3"]
    assert urllib3 == [(
        "urllib3", "2.0.7", "CVE-2023-43804", "MEDIUM",
        "urllib3 < 2.0.7 — cookie & auth header leak on cross-origin redirect.",
    )]
    # requests CVE-2023-32681 (fixed in 2.31.0): health prefixes now
    # cover every vulnerable 2.x line, not just 2.28.x.
    assert ("2.28.", "CVE-2023-32681", "HIGH") in cve_data.CVE_DB["requests"]
    assert ("2.29.", "CVE-2023-32681", "HIGH") in cve_data.CVE_DB["requests"]
    assert ("2.30.", "CVE-2023-32681", "HIGH") in cve_data.CVE_DB["requests"]
    assert len(cve_data.CVE_DB["requests"]) == 3


def test_repo_own_pins_are_not_flagged_by_either_scanner():
    """Sanity: the versions this repo itself pins (requirements.txt /
    package.json, late Nov 2026) must not trip the refreshed maps."""
    text_cache = {
        "requirements.txt": (
            "requests==2.34.2\nfastapi==0.115.0\nPyJWT==2.13.0\n"
            "pillow==12.3.0\nurllib3==2.7.0\nJinja2==3.1.6\nPyYAML==6.0.3\n"
            "cryptography==50.0.0\n"
        ),
        "package.json": '{"dependencies": {"axios": "^1.18.0", "vite": "^6.4.3"}}',
    }
    assert chc._scan_dependencies(text_cache) == []
    assert scan_bug_hunt(text_cache) == []


# ── (b) live OSV result wins over the static map ────────────────────
@pytest.mark.asyncio
async def test_live_osv_result_wins_over_static_map_npm_axios():
    # axios 0.21.0 WOULD be flagged by the static map (prefix "0.21.",
    # CVE-2021-3749) — the live result must replace it, not add to it.
    text_cache = {"package.json": '{"dependencies": {"axios": "^0.21.0"}}'}
    mocked = {"npm:axios:0.21.0": [
        {"id": "GHSA-live1", "severity": "critical", "summary": "live"},
    ]}
    with patch.object(osv_client, "query_osv_batch", AsyncMock(return_value=mocked)), \
         patch.object(chc, "_persist_cache", AsyncMock(return_value=None)):
        await chc.resolve_live_cves(text_cache)

    findings = chc._scan_dependencies(text_cache)
    assert len(findings) == 1
    assert findings[0]["id"] == "dep::package.json:0:axios:osv"
    assert findings[0]["severity"] == "critical"
    assert "GHSA-live1" in findings[0]["message"]
    assert "CVE-2021-3749" not in findings[0]["message"]


@pytest.mark.asyncio
async def test_live_confirmed_clean_suppresses_static_flag_npm_axios():
    text_cache = {"package.json": '{"dependencies": {"axios": "^0.21.0"}}'}
    with patch.object(osv_client, "query_osv_batch",
                      AsyncMock(return_value={"npm:axios:0.21.0": []})), \
         patch.object(chc, "_persist_cache", AsyncMock(return_value=None)):
        await chc.resolve_live_cves(text_cache)

    assert chc._scan_dependencies(text_cache) == []


@pytest.mark.asyncio
async def test_live_osv_result_replaces_static_cve_id_pypi_requests():
    text_cache = {"requirements.txt": "requests==2.28.1\n"}
    mocked = {"pypi:requests:2.28.1": [
        {"id": "GHSA-live2", "severity": "high", "summary": "live"},
    ]}
    with patch.object(osv_client, "query_osv_batch", AsyncMock(return_value=mocked)), \
         patch.object(chc, "_persist_cache", AsyncMock(return_value=None)):
        await chc.resolve_live_cves(text_cache)

    findings = chc._scan_dependencies(text_cache)
    assert len(findings) == 1
    assert findings[0]["id"].endswith(":osv")
    assert "CVE-2023-32681" not in findings[0]["message"]  # static id gone


# ── (c) OSV timeout / failure / empty -> static fallback ────────────
@pytest.mark.parametrize("exc", [
    ExternalCallError("osv", "timed out", status=None, method="POST",
                      url=osv_client.OSV_BATCH_URL),
    ExternalCallError("osv", "POST ... failed with 503 after 2 retries",
                      status=503, method="POST", url=osv_client.OSV_BATCH_URL),
    ExternalCallError("osv", "circuit breaker OPEN for osv",
                      method="POST", url=osv_client.OSV_BATCH_URL),
])
@pytest.mark.asyncio
async def test_osv_failure_falls_back_to_static_map(exc):
    text_cache = {"requirements.txt": "requests==2.28.0\n"}
    with patch.object(osv_client, "query_osv_batch", AsyncMock(side_effect=exc)):
        await chc.resolve_live_cves(text_cache)  # must NOT raise

    assert "pypi:requests:2.28.0" not in chc._OSV_CACHE
    findings = chc._scan_dependencies(text_cache)
    assert len(findings) == 1
    assert findings[0]["id"] == "dep::requirements.txt:1:requests"
    assert findings[0]["severity"] == "high"
    assert "CVE-2023-32681" in findings[0]["message"]


@pytest.mark.asyncio
async def test_osv_empty_result_for_package_falls_back_to_static_map():
    # OSV answered but returned NOTHING for this package (no cache
    # entry at all) — that is a miss, not a clean bill of health, so
    # the static fallback must still fire.
    text_cache = {"requirements.txt": "requests==2.28.0\n"}
    with patch.object(osv_client, "query_osv_batch", AsyncMock(return_value={})), \
         patch.object(chc, "_persist_cache", AsyncMock(return_value=None)):
        await chc.resolve_live_cves(text_cache)

    assert "pypi:requests:2.28.0" not in chc._OSV_CACHE
    findings = chc._scan_dependencies(text_cache)
    assert len(findings) == 1
    assert findings[0]["id"] == "dep::requirements.txt:1:requests"


@pytest.mark.asyncio
async def test_osv_timeout_falls_back_to_static_map(monkeypatch):
    # Same mechanism as the iter404 budget test, but asserting the
    # fallback finding for the REFRESHED static map (requests 2.28.0).
    import asyncio
    import time

    monkeypatch.setattr(chc, "OSV_TOTAL_TIMEOUT_S", 0.3)

    async def hung_batch(pkgs):
        await asyncio.sleep(999)

    monkeypatch.setattr(osv_client, "query_osv_batch", hung_batch)

    start = time.monotonic()
    await chc.resolve_live_cves({"requirements.txt": "requests==2.28.0\n"})
    assert time.monotonic() - start < 2.0
    assert "pypi:requests:2.28.0" not in chc._OSV_CACHE

    findings = chc._scan_dependencies({"requirements.txt": "requests==2.28.0\n"})
    assert len(findings) == 1
    assert findings[0]["id"] == "dep::requirements.txt:1:requests"


# ── (d) version comparison still works (real _vercmp semantics) ─────
def test_vercmp_refreshed_urllib3_boundary():
    assert _vercmp("2.0.6", "2.0.7") < 0
    assert _vercmp("2.0.7", "2.0.7") == 0
    assert _vercmp("2.8.0", "2.0.7") > 0
    assert _vercmp("2.30.9", "2.31.0") < 0
    assert _vercmp("2.31.0", "2.31.0") == 0


def test_bug_hunt_flags_below_fixed_version_only():
    below = scan_bug_hunt({"requirements.txt": "requests==2.30.9\nurllib3==2.0.6\n"})
    titles = _cve_titles(below)
    assert "requests==2.30.9" in titles
    assert "urllib3==2.0.6" in titles

    at_fixed = scan_bug_hunt({"requirements.txt": "requests==2.31.0\nurllib3==2.0.7\n"})
    assert at_fixed == []

    above = scan_bug_hunt({"requirements.txt": "requests==2.34.2\nurllib3==2.7.0\n"})
    assert above == []


def test_bug_hunt_package_json_range_uses_base_version():
    flagged = scan_bug_hunt({"package.json": '{"dependencies": {"axios": "^1.4.0"}}'})
    assert any("axios" in f["title"] for f in flagged)

    at_fixed = scan_bug_hunt({"package.json": '{"dependencies": {"axios": "^1.6.0"}}'})
    assert at_fixed == []


def test_health_prefix_match_uses_refreshed_prefixes():
    # Static fallback path only (no live cache): 2.29.x/2.30.x now flag
    # via the refreshed prefixes; 2.31.0 (the fix release) does not.
    for ver in ("2.28.0", "2.29.0", "2.30.5"):
        findings = chc._scan_dependencies({"requirements.txt": f"requests=={ver}\n"})
        assert len(findings) == 1, ver
        assert findings[0]["id"] == "dep::requirements.txt:1:requests"
        assert "CVE-2023-32681" in findings[0]["message"]

    assert chc._scan_dependencies({"requirements.txt": "requests==2.31.0\n"}) == []
    assert chc._scan_dependencies({"requirements.txt": "requests==2.34.2\n"}) == []
