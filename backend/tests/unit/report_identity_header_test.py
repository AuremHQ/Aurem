"""
tests/unit/report_identity_header_test.py — 2026-09-21

Locks in the report-identity fix (services/health_reporter.py) at
every real report-rendering surface, so a future refactor can't
silently drop the header again and reopen the mis-attribution risk
this follows up on.
"""
from __future__ import annotations

import inspect

import pytest

from services.health_reporter import format_identity_header, self_repo_identity, utc_now_iso


def test_format_identity_header_matches_the_locked_shape():
    header = format_identity_header("acme", "widgets", "main", "abcdef1234567890", "customer")
    assert header == (
        "Repo: acme/widgets@main | SHA: abcdef12 | Scan: customer | "
        f"Generated: {header.split('Generated: ', 1)[1]}"
    )
    assert header.startswith("Repo: acme/widgets@main | SHA: abcdef12 | Scan: customer | Generated: ")


def test_format_identity_header_truncates_sha_to_eight_chars():
    header = format_identity_header("a", "b", "main", "0123456789abcdef", "self")
    assert "SHA: 01234567 |" in header


def test_format_identity_header_handles_missing_sha():
    header = format_identity_header("a", "b", "main", None, "self")
    assert "SHA: unknown |" in header


def test_format_identity_header_rejects_bad_scan_kind():
    with pytest.raises(ValueError):
        format_identity_header("a", "b", "main", "sha", "bogus")


def test_self_repo_identity_reads_real_git_state():
    owner, repo, branch, head_sha = self_repo_identity("/app")
    assert owner != "unknown"
    assert repo != "unknown"
    assert branch != "unknown"
    assert head_sha and len(head_sha) == 40


def test_self_repo_identity_degrades_gracefully_for_a_non_git_dir(tmp_path):
    owner, repo, branch, head_sha = self_repo_identity(str(tmp_path))
    assert owner == "unknown"
    assert repo == "unknown"
    assert branch == "unknown"
    assert head_sha is None


@pytest.mark.asyncio
async def test_get_health_score_response_carries_identity_header(monkeypatch):
    """Wiring check #1 — services/health_score.py::get_health_score()
    is AUREM's own self-scan report and must always be self-describing."""
    import services.health_score as hs

    async def _fake_scored(*a, **kw):
        return {"status": "unscored"}

    for name in ("score_security", "score_bug_density", "score_reliability",
                 "score_test_coverage", "score_code_quality", "score_data_handling",
                 "score_performance", "score_architecture", "score_devops_infra"):
        monkeypatch.setattr(hs, name, _fake_scored)
    import services.architecture_health as ah
    monkeypatch.setattr(ah, "run_health_report", lambda: {})

    result = await hs.get_health_score(db=None)
    assert result["identity_header"].startswith("Repo: ")
    assert "| Scan: self |" in result["identity_header"]


def test_codebase_health_scan_endpoint_wires_the_identity_header():
    """Wiring check #2 — routers/codebase_health.py::scan() (a
    connected CUSTOMER repo scan) must include an `identity_header`
    with scan_kind='customer'. Source-level check (not a full mocked
    GitHub+DB call) — same convention as this codebase's other
    integration-heavy wiring guards."""
    src = inspect.getsource(__import__("routers.codebase_health", fromlist=["scan"]).scan)
    assert "format_identity_header" in src
    assert '"customer"' in src
    assert '"identity_header": identity_header' in src


def test_self_scan_script_wires_the_identity_header():
    """Wiring check #3 — scripts/self_scan.py's markdown report must
    lead with the identity header (scan_kind='self'). Source check
    only: the script has an unrelated, pre-existing import error
    (`routers.codebase_health` no longer exports `_SKIP_DIRS` — not
    introduced by this fix) that already prevented it from running
    before this change; fixing that is out of scope here."""
    src = open("scripts/self_scan.py").read()
    assert "format_identity_header" in src
    assert 'self_repo_identity(str(REPO_ROOT))' in src
    assert '"self")' in src
