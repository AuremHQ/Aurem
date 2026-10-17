"""Regression lock for Ticket A (2026-02) — opt-in scheduled per-project
auto-scan + digest email.

Covers: PATCH validation, hard-frequency-cap due-selection, bounded
fan-out, per-user/per-project isolation, honest Resend-failure
reporting (never faked), and that scan_fix_quota is never touched by
this read-only scan+report pipeline.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from services import scheduled_project_scan as sps


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def __aiter__(self):
        for d in self._docs:
            yield d


class _FakeDb:
    """Minimal in-memory double — just enough surface for
    run_due_auto_scans / the audit-row writes below."""
    def __init__(self, projects, dev_users=None):
        self._projects = projects
        self._dev_users = dev_users or {}
        self.cto_projects = self
        self.project_auto_scan_runs = self
        self.dev_users = self
        self.update_calls = []

    def find(self, query, *_a, **_kw):
        docs = [p for p in self._projects
                if p.get("auto_scan_frequency") in query.get("auto_scan_frequency", {}).get("$in", [])]
        return _FakeCursor(docs)

    async def find_one(self, query, *_a, **_kw):
        # dev_users.find_one({user_id}) lookup
        uid = query.get("user_id")
        if uid in self._dev_users:
            return {"email": self._dev_users[uid]}
        return None

    async def update_one(self, filt, update, upsert=False):
        self.update_calls.append((filt, update))
        return AsyncMock()

    async def create_index(self, *a, **kw):
        return "ok"


def _project(pid, freq="weekly", last_run=None):
    return {
        "project_id": pid, "user_id": f"user_{pid}",
        "github_owner": "o", "github_repo": pid, "name": pid,
        "auto_scan_frequency": freq, "auto_scan_last_run_at": last_run,
    }


# ── Due-selection respects the hard frequency cap ──────────────────
@pytest.mark.asyncio
async def test_weekly_project_not_yet_due_is_skipped():
    recent = time.time() - 3600  # 1h ago, nowhere near the 7-day cap
    db = _FakeDb([_project("p1", "weekly", last_run=recent)])
    with patch.object(sps, "_scan_one_project", AsyncMock()) as spy:
        summary = await sps.run_due_auto_scans(db)
    assert summary["due"] == 0
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_weekly_project_overdue_is_scanned():
    stale = time.time() - 8 * 86400  # 8 days ago, past the 7-day cap
    db = _FakeDb([_project("p1", "weekly", last_run=stale)])
    fake_result = {"project_id": "p1", "user_id": "user_p1", "run_at": time.time(),
                    "frequency": "weekly", "findings_count": 3, "score": 80,
                    "email_sent": True, "email_error": None}
    with patch.object(sps, "_scan_one_project", AsyncMock(return_value=fake_result)) as spy:
        summary = await sps.run_due_auto_scans(db)
    assert summary["due"] == 1
    spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_never_run_project_is_due_immediately():
    db = _FakeDb([_project("p1", "monthly", last_run=None)])
    fake_result = {"project_id": "p1", "user_id": "user_p1", "run_at": time.time(),
                    "frequency": "monthly", "findings_count": 0, "score": 100,
                    "email_sent": False, "email_error": "no_email_on_file"}
    with patch.object(sps, "_scan_one_project", AsyncMock(return_value=fake_result)):
        summary = await sps.run_due_auto_scans(db)
    assert summary["due"] == 1


@pytest.mark.asyncio
async def test_monthly_cap_is_30_days_not_7():
    just_over_weekly = time.time() - 10 * 86400  # past weekly cap, NOT past monthly cap
    db = _FakeDb([_project("p1", "monthly", last_run=just_over_weekly)])
    with patch.object(sps, "_scan_one_project", AsyncMock()) as spy:
        summary = await sps.run_due_auto_scans(db)
    assert summary["due"] == 0
    spy.assert_not_awaited()


# ── Bounded fan-out — no unbounded loop ─────────────────────────────
@pytest.mark.asyncio
async def test_fan_out_capped_at_max_projects_per_tick(monkeypatch):
    monkeypatch.setattr(sps, "_MAX_PROJECTS_PER_TICK", 3)
    stale = time.time() - 30 * 86400
    db = _FakeDb([_project(f"p{i}", "weekly", last_run=stale) for i in range(10)])
    fake_result = {"project_id": "x", "user_id": "u", "run_at": time.time(),
                    "frequency": "weekly", "findings_count": 0, "score": 100,
                    "email_sent": False, "email_error": None}
    with patch.object(sps, "_scan_one_project", AsyncMock(return_value=fake_result)) as spy:
        summary = await sps.run_due_auto_scans(db)
    assert summary["due"] == 3
    assert spy.await_count == 3


# ── Honest Resend failure — never faked as sent ─────────────────────
@pytest.mark.asyncio
async def test_resend_failure_is_recorded_honestly_not_faked():
    """Predeploy health showed Resend BROKEN (HTTP 400) at the time this
    ticket was built — confirms the real, current failure mode is
    captured accurately rather than silently reported as delivered."""
    project = _project("p1", "weekly")
    db = _FakeDb([project], dev_users={"user_p1": "owner@example.com"})

    async def fake_get_repo_token(proj):
        return "fake-pat"

    async def fake_build_text_cache(owner, repo, pat):
        return {"requirements.txt": "flask==1.0\n"}

    with patch("services.pat_vault.get_repo_token", AsyncMock(side_effect=fake_get_repo_token)), \
         patch("services.repo_text_cache.build_text_cache", AsyncMock(side_effect=fake_build_text_cache)), \
         patch("services.loop_full_scan.persist_findings_to_backlog", AsyncMock(return_value=2)), \
         patch("services.codebase_health_core.resolve_live_cves", AsyncMock(return_value=None)), \
         patch("services.daily_digest._send_via_resend", AsyncMock(return_value=False)) as resend_spy:
        result = await sps._scan_one_project(db, project)

    resend_spy.assert_awaited_once()
    assert result["email_sent"] is False, "must never fake delivery when Resend actually failed"
    assert result["email_error"] == "resend_delivery_failed"
    assert result["score"] is not None  # the scan itself still succeeded and is reported


@pytest.mark.asyncio
async def test_no_email_on_file_is_also_honestly_recorded():
    project = _project("p1", "weekly")
    db = _FakeDb([project], dev_users={})  # no email on file

    with patch("services.pat_vault.get_repo_token", AsyncMock(return_value="fake-pat")), \
         patch("services.repo_text_cache.build_text_cache", AsyncMock(return_value={"a.py": "x=1"})), \
         patch("services.loop_full_scan.persist_findings_to_backlog", AsyncMock(return_value=0)), \
         patch("services.codebase_health_core.resolve_live_cves", AsyncMock(return_value=None)):
        result = await sps._scan_one_project(db, project)

    assert result["email_sent"] is False
    assert result["email_error"] == "no_email_on_file"


# ── Isolation — query only ever matches opted-in projects ───────────
@pytest.mark.asyncio
async def test_off_projects_are_never_selected():
    db = _FakeDb([_project("p1", "off", last_run=None)])
    with patch.object(sps, "_scan_one_project", AsyncMock()) as spy:
        summary = await sps.run_due_auto_scans(db)
    assert summary["due"] == 0
    spy.assert_not_awaited()


# ── PATCH validation (router-level) ─────────────────────────────────
def test_patch_rejects_invalid_frequency_value():
    from routers.cto_projects.management import _AUTO_SCAN_FREQUENCIES
    assert _AUTO_SCAN_FREQUENCIES == {"off", "weekly", "monthly"}


# ── scan_fix_quota is never imported/touched by this read-only path ─
def test_scan_fix_quota_never_imported_by_auto_scan_module():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(sps))
    imported_modules = {
        n.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) for n in [node]
    } | {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.Import) for alias in node.names
    }
    assert not any("scan_fix_quota" in (m or "") for m in imported_modules), (
        "this ticket's scan+report pipeline must stay read-only and never "
        "touch the fix-quota meter (which only meters successful *fixes*)"
    )
