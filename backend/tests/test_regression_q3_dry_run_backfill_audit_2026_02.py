"""
tests/test_regression_q3_dry_run_backfill_audit_2026_02.py — Q3 (2026-02)

DRY-RUN ONLY audit: lists existing users who are still exhausted
under the NEW (post-Q1) budget, have >=1 connected project, and are
not dormant. MUST NEVER write anything. MUST exclude dormant users
and users without projects.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.quota_backfill_audit import run_dry_run_audit, _severity_bucket
from config.quota_limits import EXISTING_USER_TOPUP_USD


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
    def limit(self, n):
        return self
    async def to_list(self, n):
        return self._rows


def _mk_db(users, usage_by_id, project_counts):
    db = MagicMock()
    db.dev_users.find = MagicMock(return_value=_Cursor(users))

    async def _count_documents(query):
        return project_counts.get(query.get("user_id"), 0)
    db.cto_projects.count_documents = AsyncMock(side_effect=_count_documents)

    async def _fake_get_usage(user_id):
        return usage_by_id[user_id]

    db._fake_get_usage = _fake_get_usage
    return db


@pytest.mark.asyncio
async def test_dry_run_never_writes_anything():
    users = [{"user_id": "u1", "email": "a@x.com", "last_seen_at": 100}]
    usage = {"u1": {"is_exhausted_usd": True, "used_usd": 1.0, "effective_limit_usd": 0.25}}
    db = _mk_db(users, usage, {"u1": 2})
    with patch("services.quota_backfill_audit.get_usage", db._fake_get_usage):
        result = await run_dry_run_audit(db)
    assert result["dry_run"] is True
    assert result["applied_any_grant"] is False
    # Confirm no write-capable mongo method was ever invoked on the mock
    for attr_name in ("insert_one", "update_one", "delete_one", "find_one_and_update"):
        for coll in (db.dev_users, db.cto_projects, getattr(db, "cto_token_grants", MagicMock())):
            m = getattr(coll, attr_name, None)
            if isinstance(m, (AsyncMock, MagicMock)):
                m.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_excludes_users_without_a_project():
    users = [{"user_id": "u1", "email": "a@x.com", "last_seen_at": 100}]
    usage = {"u1": {"is_exhausted_usd": True, "used_usd": 1.0, "effective_limit_usd": 0.25}}
    db = _mk_db(users, usage, {"u1": 0})  # zero projects
    with patch("services.quota_backfill_audit.get_usage", db._fake_get_usage):
        result = await run_dry_run_audit(db)
    assert result["matched_count"] == 0


@pytest.mark.asyncio
async def test_dry_run_excludes_users_not_exhausted_under_new_budget():
    """Core Q3 point: raising the free cap in Q1 means most previously
    -exhausted users are no longer exhausted — only genuinely still
    -stuck users should show up here."""
    users = [{"user_id": "u1", "email": "a@x.com", "last_seen_at": 100}]
    usage = {"u1": {"is_exhausted_usd": False, "used_usd": 0.05, "effective_limit_usd": 0.25}}
    db = _mk_db(users, usage, {"u1": 1})
    with patch("services.quota_backfill_audit.get_usage", db._fake_get_usage):
        result = await run_dry_run_audit(db)
    assert result["matched_count"] == 0


@pytest.mark.asyncio
async def test_dry_run_query_excludes_dormant_users_at_the_db_filter():
    """Dormant (never-logged-in) users must be excluded by the Mongo
    query itself (last_seen_at must exist), so they can never reach
    the proposed-topup list."""
    captured = {}

    def _find(query, *_a, **_kw):
        captured["query"] = query
        return _Cursor([])
    db = MagicMock()
    db.dev_users.find = MagicMock(side_effect=_find)
    await run_dry_run_audit(db)
    filt = captured["query"]["$and"]
    assert any(
        "last_seen_at" in clause and clause["last_seen_at"].get("$exists") is True
        for clause in filt
    )


@pytest.mark.asyncio
async def test_dry_run_groups_by_severity_and_proposes_config_topup():
    users = [
        {"user_id": "u1", "email": "a@x.com", "last_seen_at": 100},
        {"user_id": "u2", "email": "b@x.com", "last_seen_at": 100},
    ]
    usage = {
        "u1": {"is_exhausted_usd": True, "used_usd": 0.30, "effective_limit_usd": 0.25},  # ~1.2x
        "u2": {"is_exhausted_usd": True, "used_usd": 2.00, "effective_limit_usd": 0.25},  # 8x
    }
    db = _mk_db(users, usage, {"u1": 1, "u2": 1})
    with patch("services.quota_backfill_audit.get_usage", db._fake_get_usage):
        result = await run_dry_run_audit(db)
    assert result["matched_count"] == 2
    assert result["grouped_by_severity"]["slightly_over_under_2x"] == 1
    assert result["grouped_by_severity"]["heavily_over_5x_plus"] == 1
    for u in result["users"]:
        assert u["proposed_topup_usd"] == EXISTING_USER_TOPUP_USD
    assert result["proposed_topup_usd_each"] == EXISTING_USER_TOPUP_USD


def test_severity_bucket_boundaries():
    assert _severity_bucket(0.10, 0.25) == "slightly_over_under_2x"
    assert _severity_bucket(0.75, 0.25) == "moderately_over_2x_to_5x"
    assert _severity_bucket(2.0, 0.25) == "heavily_over_5x_plus"
    assert _severity_bucket(1.0, 0.0) == "unknown"


@pytest.mark.asyncio
async def test_dry_run_endpoint_is_admin_gated():
    """The router this lives on (routers/admin_users.py) already
    carries a router-level admin dependency (Iter 358 defense-in
    -depth) — confirm the new route is on that router, not a
    separate un-gated one."""
    import routers.admin_users as admin_mod
    paths = [r.path for r in admin_mod.router.routes]
    assert "/admin/quota/exhausted-users-dry-run" in paths
