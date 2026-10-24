"""tests/test_regression_iter2026_13b_free_scan_funnel.py

Master Prompt v2 PART 4.1 — the free scan funnel.

Per the user's clarification, "the scan" is the EXISTING auto-
triggered pipeline (services/project_onboarding_scan.py::
run_onboarding_scan, wired at routers/cto_projects/management.py on
successful repo connect) — this file does NOT add a new trigger. It
adds:
  - services/free_scan_results.py: a cost-capped category runner
    (FREE_SCAN_COST_CAP_USD, graceful partial-scan degradation — a
    cost-exceeded category is marked "not run", the scan never
    fails outright), a rolling-average cost tracker + >5x alarm, and
    persistence of the FULL results (every severity, unlike
    cto_open_findings which only keeps critical/high/medium/unknown)
    keyed one-per-project so re-connecting the same project can't
    burn a second free scan.
  - A new GET results endpoint, ownership-checked the same way every
    other project-scoped endpoint in routers/cto_projects/
    management.py already is (db.cto_projects.find_one({"project_id":
    ..., "user_id": ...})) — IDOR-safe by construction, verified
    below anyway.

TDD: written and run RED before services/free_scan_results.py exists.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)


def _uid(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def db():
    if not _live_available:
        pytest.skip("MONGO_URL/DB_NAME not set")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    database = client[DB_NAME]
    from cto_services import db as _dbmod
    _dbmod.set_db(database)
    yield database
    _dbmod.set_db(None)
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.free_scan_results.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.cto_projects.delete_many({"user_id": {"$regex": "^wallet_test_"}})


async def _make_project(db, *, owner_id):
    pid = f"proj_{uuid.uuid4().hex[:10]}"
    await db.cto_projects.insert_one({
        "project_id": pid, "user_id": owner_id,
        "github_owner": "someorg", "github_repo": "somerepo",
    })
    return pid


def _fake_categories(n=4):
    def _mk(i):
        def _scan(text_cache):
            return [{"rule_id": f"rule_{i}", "severity": "low" if i else "critical",
                     "file": "src/app.py", "line": 10 + i}]
        return _scan
    return {f"cat_{i}": _mk(i) for i in range(n)}


class TestCostCappedCategoryRunner:
    async def test_all_categories_run_when_under_cap(self):
        from services.free_scan_results import run_categories_with_cost_cap
        cats = _fake_categories(4)
        findings, ran, not_ran, _cost = run_categories_with_cost_cap(
            cats, text_cache={"a.py": "x"}, cap_usd=1.00, cost_per_category_usd=0.001,
        )
        assert set(ran) == set(cats.keys())
        assert not_ran == []
        assert len(findings) == 4

    async def test_remaining_categories_marked_not_run_when_cap_exceeded(self):
        from services.free_scan_results import run_categories_with_cost_cap
        cats = _fake_categories(10)
        findings, ran, not_ran, cost = run_categories_with_cost_cap(
            cats, text_cache={"a.py": "x"}, cap_usd=0.0025, cost_per_category_usd=0.001,
        )
        assert len(ran) == 2
        assert len(not_ran) == 8
        assert cost <= 0.0025
        # Scan never "fails" outright — findings from the categories
        # that DID run are still returned.
        assert len(findings) == 2

    async def test_zero_cap_runs_nothing_but_does_not_raise(self):
        from services.free_scan_results import run_categories_with_cost_cap
        cats = _fake_categories(3)
        findings, ran, not_ran, _cost = run_categories_with_cost_cap(
            cats, text_cache={}, cap_usd=0.0, cost_per_category_usd=0.001,
        )
        assert ran == []
        assert len(not_ran) == 3
        assert findings == []

    async def test_a_category_that_raises_does_not_abort_the_rest(self):
        from services.free_scan_results import run_categories_with_cost_cap
        def _boom(_tc):
            raise RuntimeError("scanner bug")
        cats = {"broken": _boom, **_fake_categories(2)}
        findings, ran, _not_ran, _cost = run_categories_with_cost_cap(
            cats, text_cache={}, cap_usd=1.0, cost_per_category_usd=0.001,
        )
        assert "broken" in ran  # attempted, counted, just yielded no findings
        assert len(findings) == 2


class TestCostAlarm:
    async def test_no_alarm_with_no_rolling_history(self, db):
        from services.free_scan_results import compute_cost_alarm
        assert compute_cost_alarm(0.9, None) is False
        assert compute_cost_alarm(0.9, 0.0) is False

    async def test_alarm_above_5x_rolling_average(self):
        from services.free_scan_results import compute_cost_alarm
        assert compute_cost_alarm(0.51, 0.1) is True
        assert compute_cost_alarm(0.49, 0.1) is False


class TestOneFreeScanPerProjectPerAccount:
    async def test_second_start_for_same_project_is_a_noop(self, db):
        from services.free_scan_results import start_free_scan
        u = _uid("fs")
        pid = await _make_project(db, owner_id=u)
        started_1 = await start_free_scan(db, user_id=u, project_id=pid)
        started_2 = await start_free_scan(db, user_id=u, project_id=pid)
        assert started_1 is True
        assert started_2 is False

    async def test_concurrent_starts_for_same_project_only_one_wins(self, db):
        import asyncio

        from services.free_scan_results import start_free_scan
        u = _uid("fsrace")
        pid = await _make_project(db, owner_id=u)
        results = await asyncio.gather(*[
            start_free_scan(db, user_id=u, project_id=pid) for _ in range(10)
        ])
        assert sum(1 for r in results if r) == 1


class TestFinishAndReadResults:
    async def test_finish_persists_all_severities_not_just_critical_high(self, db):
        from services.free_scan_results import (
            finish_free_scan,
            get_free_scan_results,
            start_free_scan,
        )
        u = _uid("fsfull")
        pid = await _make_project(db, owner_id=u)
        await start_free_scan(db, user_id=u, project_id=pid)
        findings = [
            {"rule_id": "r1", "severity": "critical", "file": "a.py", "line": 1},
            {"rule_id": "r2", "severity": "low", "file": "b.py", "line": 2},
            {"rule_id": "r3", "severity": "info", "file": "c.py", "line": 3},
        ]
        await finish_free_scan(
            db, user_id=u, project_id=pid, score=41, findings=findings,
            categories_run=["security"], categories_not_run=[], cost_usd=0.002,
        )
        result = await get_free_scan_results(db, user_id=u, project_id=pid)
        assert result["status"] == "ready"
        assert result["score"] == 41
        assert len(result["issues"]) == 3  # low + info survive, unlike cto_open_findings
        assert result["severity_counts"]["low"] == 1
        assert result["severity_counts"]["info"] == 1

    async def test_scanning_status_visible_before_finish(self, db):
        from services.free_scan_results import get_free_scan_results, start_free_scan
        u = _uid("fsprog")
        pid = await _make_project(db, owner_id=u)
        await start_free_scan(db, user_id=u, project_id=pid)
        result = await get_free_scan_results(db, user_id=u, project_id=pid)
        assert result["status"] == "scanning"

    async def test_share_card_summary_is_score_only(self, db):
        from services.free_scan_results import (
            finish_free_scan,
            get_free_scan_results,
            start_free_scan,
        )
        u = _uid("fsshare")
        pid = await _make_project(db, owner_id=u)
        await start_free_scan(db, user_id=u, project_id=pid)
        await finish_free_scan(
            db, user_id=u, project_id=pid, score=41,
            findings=[{"rule_id": "r1", "severity": "high", "file": "<script>x</script>.py", "line": 1}],
            categories_run=["security"], categories_not_run=[], cost_usd=0.001,
        )
        result = await get_free_scan_results(db, user_id=u, project_id=pid)
        assert result["share_card"] == {"score": 41}


class TestOwnershipIDOR:
    async def test_other_user_cannot_read_results_for_a_project_they_do_not_own(self, db):
        from services.free_scan_results import (
            finish_free_scan,
            get_free_scan_results,
            start_free_scan,
        )
        owner = _uid("fsowner")
        attacker = _uid("fsattacker")
        pid = await _make_project(db, owner_id=owner)
        await start_free_scan(db, user_id=owner, project_id=pid)
        await finish_free_scan(
            db, user_id=owner, project_id=pid, score=10,
            findings=[], categories_run=[], categories_not_run=[], cost_usd=0.0,
        )
        result = await get_free_scan_results(db, user_id=attacker, project_id=pid)
        assert result is None

    async def test_nonexistent_project_returns_none(self, db):
        from services.free_scan_results import get_free_scan_results
        u = _uid("fsghost")
        result = await get_free_scan_results(db, user_id=u, project_id="does-not-exist")
        assert result is None


class TestFreeScanRouterEndpoint:
    async def test_endpoint_returns_results_for_owner(self, db):
        from services.free_scan_results import finish_free_scan, start_free_scan
        u = _uid("fsroute")
        pid = await _make_project(db, owner_id=u)
        await start_free_scan(db, user_id=u, project_id=pid)
        await finish_free_scan(
            db, user_id=u, project_id=pid, score=55, findings=[],
            categories_run=[], categories_not_run=[], cost_usd=0.0,
        )

        from routers.cto_projects import free_scan as free_scan_router

        async def _fake_current_dev(authorization=None):
            return {"user_id": u}
        import unittest.mock
        with unittest.mock.patch.object(free_scan_router, "current_dev", _fake_current_dev):
            resp = await free_scan_router.get_free_scan(pid, authorization="Bearer x")
        assert resp["score"] == 55

    async def test_endpoint_404s_for_a_project_the_caller_does_not_own(self, db):
        from services.free_scan_results import finish_free_scan, start_free_scan
        owner = _uid("fsrouteowner")
        attacker = _uid("fsrouteattacker")
        pid = await _make_project(db, owner_id=owner)
        await start_free_scan(db, user_id=owner, project_id=pid)
        await finish_free_scan(
            db, user_id=owner, project_id=pid, score=55, findings=[],
            categories_run=[], categories_not_run=[], cost_usd=0.0,
        )

        from fastapi import HTTPException

        from routers.cto_projects import free_scan as free_scan_router

        async def _fake_current_dev(authorization=None):
            return {"user_id": attacker}
        import unittest.mock
        with unittest.mock.patch.object(free_scan_router, "current_dev", _fake_current_dev), \
                pytest.raises(HTTPException) as exc:
            await free_scan_router.get_free_scan(pid, authorization="Bearer x")
        assert exc.value.status_code == 404

    async def test_endpoint_rate_limited_per_account_and_ip(self, db, monkeypatch):
        from services.free_scan_results import finish_free_scan, start_free_scan
        u = _uid("fsrl")
        pid = await _make_project(db, owner_id=u)
        await start_free_scan(db, user_id=u, project_id=pid)
        await finish_free_scan(
            db, user_id=u, project_id=pid, score=1, findings=[],
            categories_run=[], categories_not_run=[], cost_usd=0.0,
        )

        from fastapi import HTTPException

        from routers.cto_projects import free_scan as free_scan_router
        monkeypatch.setattr(free_scan_router, "_FREE_SCAN_READ_PER_MIN", 2)

        async def _fake_current_dev(authorization=None):
            return {"user_id": u}
        import unittest.mock
        with unittest.mock.patch.object(free_scan_router, "current_dev", _fake_current_dev):
            hit_429 = False
            for _ in range(6):
                try:
                    await free_scan_router.get_free_scan(pid, authorization="Bearer x")
                except HTTPException as e:
                    if e.status_code == 429:
                        hit_429 = True
                        break
        assert hit_429
