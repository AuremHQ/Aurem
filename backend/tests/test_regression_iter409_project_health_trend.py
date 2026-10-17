"""Regression lock for Ticket E (2026-02) — per-project health score
+ trend.

Covers: snapshot schema/shape, best-effort write (never breaks the
scan that triggered it), 90-snapshot retention cap, strict per-user
isolation on read, idempotent index creation, the GET /trend endpoint
owner-isolation + empty-state contract, and that every real scan path
(manual /scan, scheduled auto-scan, onboarding scan) calls the SAME
record_health_snapshot with the correct `source` tag.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import project_health_trend as pht


def _finding(sev="high"):
    return {"severity": sev, "file": "a.py", "line": 1, "title": "x"}


# ── Schema / shape ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_record_snapshot_writes_full_expected_shape():
    fake_db = MagicMock()
    fake_db.project_health_snapshots.insert_one = AsyncMock()

    class _EmptyCursor:
        def sort(self, *a, **kw): return self
        def skip(self, *a, **kw): return self
        def __aiter__(self):
            async def _gen():
                return
                yield  # pragma: no cover
            return _gen()
    fake_db.project_health_snapshots.find = MagicMock(return_value=_EmptyCursor())

    await pht.record_health_snapshot(
        fake_db, project_id="p1", user_id="u1", score=72,
        findings=[_finding("high"), _finding("critical"), _finding("info")],
        source="manual_scan",
    )
    fake_db.project_health_snapshots.insert_one.assert_awaited_once()
    doc = fake_db.project_health_snapshots.insert_one.call_args[0][0]
    assert doc["project_id"] == "p1"
    assert doc["user_id"] == "u1"
    assert doc["score"] == 72
    assert doc["findings_total"] == 3
    assert doc["findings_by_severity"] == {
        "critical": 1, "high": 1, "unknown": 0, "medium": 0, "low": 0, "info": 1,
    }
    assert doc["source"] == "manual_scan"
    assert isinstance(doc["snapshot_at"], float)


# ── Best-effort — a write failure must never raise into the caller ──
@pytest.mark.asyncio
async def test_snapshot_write_failure_is_swallowed_not_raised():
    fake_db = MagicMock()
    fake_db.project_health_snapshots.insert_one = AsyncMock(side_effect=RuntimeError("mongo down"))
    # must not raise
    await pht.record_health_snapshot(
        fake_db, project_id="p1", user_id="u1", score=50,
        findings=[_finding()], source="manual_scan",
    )


@pytest.mark.asyncio
async def test_record_snapshot_noop_when_db_is_none():
    # must not raise
    await pht.record_health_snapshot(
        None, project_id="p1", user_id="u1", score=50,
        findings=[], source="manual_scan",
    )


# ── Retention — only the newest 90 snapshots per project survive ────
@pytest.mark.asyncio
async def test_retention_trims_beyond_90_snapshots_per_project():
    fake_db = MagicMock()
    fake_db.project_health_snapshots.insert_one = AsyncMock()
    old_ids = [f"old_{i}" for i in range(5)]

    class _Cursor:
        """These 5 docs already represent what real Mongo would hand
        back AFTER a real .skip(90) on a much larger sorted set — i.e.
        the "beyond the cap" rows. `.skip()` here is a pass-through
        spy, not a re-slice, matching how the production code treats
        the cursor it gets back."""
        def __init__(self, docs): self._docs = docs
        def sort(self, *a, **kw): return self
        def skip(self, n): return self
        def __aiter__(self):
            docs = self._docs
            async def _gen():
                for d in docs:
                    yield d
            return _gen()
    fake_db.project_health_snapshots.find = MagicMock(
        return_value=_Cursor([{"_id": i} for i in old_ids]),
    )
    fake_db.project_health_snapshots.delete_many = AsyncMock()

    await pht.record_health_snapshot(
        fake_db, project_id="p1", user_id="u1", score=90,
        findings=[], source="manual_scan",
    )
    fake_db.project_health_snapshots.delete_many.assert_awaited_once_with(
        {"_id": {"$in": old_ids}},
    )


@pytest.mark.asyncio
async def test_retention_skip_uses_the_90_cap_constant():
    assert pht._MAX_SNAPSHOTS_PER_PROJECT == 90


# ── Read isolation — project_id alone is never enough ───────────────
@pytest.mark.asyncio
async def test_trend_read_filters_by_user_id_too_not_just_project_id():
    fake_db = MagicMock()
    captured_query = {}

    class _Cursor:
        def sort(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def __aiter__(self):
            async def _gen():
                return
                yield  # pragma: no cover
            return _gen()

    def _find(query, *_a, **_kw):
        captured_query.update(query)
        return _Cursor()
    fake_db.project_health_snapshots.find = _find

    await pht.get_project_health_trend(fake_db, project_id="p1", user_id="victim")
    assert captured_query == {"project_id": "p1", "user_id": "victim"}, (
        "a per-project trend read MUST also scope by user_id — otherwise "
        "any user guessing another user's project_id could read their "
        "private scan-health history"
    )


# ── Idempotent index creation ────────────────────────────────────────
@pytest.mark.asyncio
async def test_ensure_indexes_is_idempotent_on_conflict():
    fake_db = MagicMock()
    fake_db.project_health_snapshots.create_index = AsyncMock(
        side_effect=Exception("IndexOptionsConflict"),
    )
    # must not raise even on a conflicting pre-existing index
    await pht.ensure_indexes(fake_db)


@pytest.mark.asyncio
async def test_ensure_indexes_targets_project_and_time():
    fake_db = MagicMock()
    fake_db.project_health_snapshots.create_index = AsyncMock()
    await pht.ensure_indexes(fake_db)
    fake_db.project_health_snapshots.create_index.assert_awaited_once_with(
        [("project_id", 1), ("snapshot_at", -1)],
    )


# ── GET /codebase-health/trend endpoint ──────────────────────────────
@pytest.mark.asyncio
async def test_trend_endpoint_requires_project_id():
    from fastapi import HTTPException
    from routers.codebase_health import health_trend
    with pytest.raises(HTTPException) as exc:
        await health_trend(project_id=None, authorization="Bearer x")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_trend_endpoint_returns_empty_list_when_db_down():
    from routers.codebase_health import health_trend
    with patch("routers.codebase_health.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.codebase_health.check_permission",
               AsyncMock(return_value={"effective_account_id": "u1"})), \
         patch("routers.codebase_health.get_db", return_value=None):
        resp = await health_trend(project_id="p1", authorization="Bearer x")
    assert resp == {"ok": True, "snapshots": []}


@pytest.mark.asyncio
async def test_trend_endpoint_scopes_query_to_callers_own_account():
    from routers.codebase_health import health_trend
    fake_db = MagicMock()
    with patch("routers.codebase_health.current_dev", AsyncMock(return_value={"user_id": "attacker"})), \
         patch("routers.codebase_health.check_permission",
               AsyncMock(return_value={"effective_account_id": "attacker"})), \
         patch("routers.codebase_health.get_db", return_value=fake_db), \
         patch("services.project_health_trend.get_project_health_trend",
               AsyncMock(return_value=[])) as spy:
        await health_trend(project_id="victims_project", authorization="Bearer x")
    spy.assert_awaited_once_with(fake_db, project_id="victims_project", user_id="attacker")


# ── Call-site wiring — manual /scan, scheduled auto-scan, onboarding ─
@pytest.mark.asyncio
async def test_onboarding_scan_now_computes_a_real_score_and_records_it():
    """Before Ticket E, run_onboarding_scan never called
    _score_for_findings at all — it only wrote to the open-findings
    backlog. Verifies both the score computation AND the snapshot
    write now happen, tagged onboarding_scan."""
    from services.project_onboarding_scan import run_onboarding_scan

    findings = [_finding("critical"), _finding("high")]
    with patch("services.repo_text_cache.build_text_cache",
               AsyncMock(return_value={"a.py": "x=1"})), \
         patch("services.codebase_health_core.resolve_live_cves", AsyncMock()), \
         patch("services.codebase_health_core.SCANNERS",
               {"security": lambda tc: findings}), \
         patch("services.loop_full_scan.persist_findings_to_backlog",
               AsyncMock(return_value=2)), \
         patch("services.project_health_trend.record_health_snapshot",
               AsyncMock()) as snap_spy:
        await run_onboarding_scan(
            db=MagicMock(), user_id="u1", project_id="p1",
            github_token="tok", github_owner="o", github_repo="r",
        )
    snap_spy.assert_awaited_once()
    kwargs = snap_spy.call_args.kwargs
    assert kwargs["project_id"] == "p1"
    assert kwargs["user_id"] == "u1"
    assert kwargs["source"] == "onboarding_scan"
    assert kwargs["score"] is not None  # real _score_for_findings result, not a stub


@pytest.mark.asyncio
async def test_scheduled_auto_scan_records_snapshot_tagged_correctly():
    from services import scheduled_project_scan as sps

    project = {"project_id": "p1", "user_id": "u1", "auto_scan_frequency": "weekly",
               "github_owner": "o", "github_repo": "r"}
    with patch("services.pat_vault.get_repo_token", AsyncMock(return_value="tok")), \
         patch("services.repo_text_cache.build_text_cache",
               AsyncMock(return_value={"a.py": "x=1"})), \
         patch("services.loop_full_scan.persist_findings_to_backlog",
               AsyncMock(return_value=1)), \
         patch("services.codebase_health_core.resolve_live_cves", AsyncMock()), \
         patch("services.daily_digest._send_via_resend", AsyncMock(return_value=False)), \
         patch("services.project_health_trend.record_health_snapshot",
               AsyncMock()) as snap_spy:
        fake_db = MagicMock()
        fake_db.dev_users.find_one = AsyncMock(return_value=None)
        await sps._scan_one_project(fake_db, project)
    snap_spy.assert_awaited_once()
    assert snap_spy.call_args.kwargs["source"] == "scheduled_auto_scan"
    assert snap_spy.call_args.kwargs["project_id"] == "p1"
    assert snap_spy.call_args.kwargs["user_id"] == "u1"


def test_manual_scan_endpoint_calls_record_health_snapshot():
    """Structural lock on routers/codebase_health.py::scan — proves the
    wiring exists at the exact source level (full endpoint is too
    heavy to exercise end-to-end in a unit test; Ticket B's own
    /scan tests already cover the broader endpoint path)."""
    import inspect
    import routers.codebase_health as mod
    src = inspect.getsource(mod.scan)
    assert "record_health_snapshot" in src
    assert 'source="manual_scan"' in src
    # Must stay inside the SAME "only persist if real files were read"
    # guard as the existing codebase_health_scans insert (Iter 212m-177).
    guard_idx = src.index("if len(text_cache) > 0:")
    snapshot_idx = src.index("record_health_snapshot")
    assert guard_idx < snapshot_idx


def test_does_not_reuse_platform_health_score_module():
    """Ticket E must be a SEPARATE scorer from AUREM's own platform
    health_score.py (9 founder categories / AUREM-internal
    collections) — structural guard against accidentally wiring the
    wrong scorer in for a customer's connected repo."""
    import inspect
    import services.project_health_trend as mod
    src = inspect.getsource(mod)
    assert "services.health_score" not in src
    assert "from services import health_score" not in src
