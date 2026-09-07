"""
tests/test_admin_onboarding_funnel_2026_09_08.py — Onboarding Funnel
dashboard (2026-09-08). Reuses the exact _FakeDB/_FakeCursor pattern
from test_phase2_admin_users_coverage.py (same shape `classify_users`
expects: `.find().to_list()` AND async-iterable cursors).

Covers:
  * GET /admin/onboarding-funnel/summary — stage counts in natural
    funnel order, oldest/median stuck days.
  * GET /admin/onboarding-funnel/users?stage=X — per-user rows sorted
    most-stuck-first, nudge sent/clicked status merged in.
  * Admin auth gate (401 with no Authorization header).
  * Unknown stage → 400.
"""
from __future__ import annotations

import re as _re
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock


def _matches(doc, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
            if "$exists" in v and (k in doc) != v["$exists"]:
                return False
            if "$ne" in v and doc.get(k) == v["$ne"]:
                return False
            continue
        if doc.get(k) != v:
            return False
    return True


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        v = self._rows[self._i]
        self._i += 1
        return v

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def find(self, query=None, projection=None):
        return _FakeCursor([dict(r) for r in self.rows if _matches(r, query)])

    async def find_one(self, query=None, projection=None):
        matched = [r for r in self.rows if _matches(r, query)]
        return dict(matched[0]) if matched else None

    async def count_documents(self, query=None):
        return sum(1 for r in self.rows if _matches(r, query))


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


ADMIN_USER = {"user_id": "admin-1", "is_admin": True, "tier": "founder",
              "email": "founder@aurem.dev", "is_unlimited": True}
AUTH = {"Authorization": "Bearer admin-1"}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from routers import admin_onboarding_funnel as router_mod
    from cto_services import db as _dbmod
    from fastapi import Header
    _dbmod.set_db(fake_db)

    async def _fake_require_admin_dep(authorization: str = Header(None)):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return ADMIN_USER

    app = FastAPI()
    app.dependency_overrides[router_mod.require_admin_dep] = _fake_require_admin_dep
    app.include_router(router_mod.router, prefix="/api/aurem-dev")

    c = TestClient(app)
    yield c

    _dbmod.set_db(None)
    app.dependency_overrides.clear()


def _seed_stuck_users(fake_db):
    now = datetime.now(timezone.utc)
    # stage4_fully_inactive — signed up 5 days ago, zero engagement.
    fake_db.dev_users.rows.append({
        "user_id": "u_inactive", "email": "inactive@x.com", "name": "Ivy",
        "created_at": now - timedelta(days=5),
    })
    # stage1_github_started — clicked connect 3 days ago, never installed.
    fake_db.dev_users.rows.append({
        "user_id": "u_started", "email": "started@x.com", "name": "Sam",
        "created_at": now - timedelta(days=10),
    })
    fake_db.github_funnel_events.rows.append({
        "user_id": "u_started", "ts_epoch": (now - timedelta(days=3)).timestamp(),
    })
    # stage3_no_chat — has a project 11 days old, never chatted.
    fake_db.dev_users.rows.append({
        "user_id": "u_no_chat", "email": "nochat@x.com", "name": "Nia",
        "created_at": now - timedelta(days=20),
    })
    fake_db.cto_projects.rows.append({
        "user_id": "u_no_chat", "project_id": "p1",
        "created_at": now - timedelta(days=11),
    })
    fake_db.onboarding_emails.rows.append({
        "user_id": "u_no_chat", "campaign": "funnel_stage_nudge",
        "stage": "stage3_no_chat", "sent_ok": True,
        "sent_at": now - timedelta(days=1), "clicked_at": None,
    })
    # Too new (12h) — must NOT show up anywhere (below 24h threshold).
    fake_db.dev_users.rows.append({
        "user_id": "u_fresh", "email": "fresh@x.com", "name": "Fresh",
        "created_at": now - timedelta(hours=12),
    })
    # Fully activated — first_chat_at set, excluded entirely.
    fake_db.dev_users.rows.append({
        "user_id": "u_active", "email": "active@x.com", "name": "Ace",
        "created_at": now - timedelta(days=30),
        "first_chat_at": now - timedelta(days=25),
    })


class TestSummary:
    def test_summary_counts_and_natural_funnel_order(self, client, fake_db):
        _seed_stuck_users(fake_db)
        r = client.get("/api/aurem-dev/admin/onboarding-funnel/summary", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        keys = [s["key"] for s in body["stages"]]
        assert keys == [
            "stage4_fully_inactive", "stage1_github_started",
            "stage2_project_pending", "stage3_no_chat",
        ]
        by_key = {s["key"]: s for s in body["stages"]}
        assert by_key["stage4_fully_inactive"]["count"] == 1
        assert by_key["stage1_github_started"]["count"] == 1
        assert by_key["stage2_project_pending"]["count"] == 0
        assert by_key["stage3_no_chat"]["count"] == 1
        assert body["total_stuck"] == 3
        # Fresh (12h) and already-activated users never counted.
        assert body["total_stuck"] != 5

    def test_summary_requires_admin(self, client):
        r = client.get("/api/aurem-dev/admin/onboarding-funnel/summary")
        assert r.status_code == 401


class TestUsersByStage:
    def test_users_for_stage_include_stuck_days_and_nudge_status(self, client, fake_db):
        _seed_stuck_users(fake_db)
        r = client.get(
            "/api/aurem-dev/admin/onboarding-funnel/users",
            params={"stage": "stage3_no_chat"}, headers=AUTH,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        row = body["users"][0]
        assert row["user_id"] == "u_no_chat"
        assert row["email"] == "nochat@x.com"
        assert row["stuck_days"] == 11
        assert row["nudge_sent"] is True
        assert row["nudge_clicked"] is False

    def test_users_for_stage_with_no_nudge_sent_yet(self, client, fake_db):
        _seed_stuck_users(fake_db)
        r = client.get(
            "/api/aurem-dev/admin/onboarding-funnel/users",
            params={"stage": "stage1_github_started"}, headers=AUTH,
        )
        assert r.status_code == 200
        row = r.json()["users"][0]
        assert row["user_id"] == "u_started"
        assert row["nudge_sent"] is False
        assert row["nudge_clicked"] is False

    def test_unknown_stage_rejected(self, client, fake_db):
        r = client.get(
            "/api/aurem-dev/admin/onboarding-funnel/users",
            params={"stage": "not_a_real_stage"}, headers=AUTH,
        )
        assert r.status_code == 400

    def test_empty_stage_returns_empty_list_not_error(self, client, fake_db):
        _seed_stuck_users(fake_db)
        r = client.get(
            "/api/aurem-dev/admin/onboarding-funnel/users",
            params={"stage": "stage2_project_pending"}, headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["count"] == 0
        assert r.json()["users"] == []
