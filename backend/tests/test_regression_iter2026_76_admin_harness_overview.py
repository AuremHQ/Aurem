"""tests/test_regression_iter2026_76_admin_harness_overview.py

Admin Harness panel (2026-11-16): two new real-data read endpoints
backing the admin side-panel "Harness" section —
GET /admin/harness/signups (email/date/signup_source) and
GET /admin/harness/telemetry-status (event count + last received).
Downloads and releases are deliberately NOT duplicated here — the
admin page reuses the existing /admin/harness/downloads and links to
the existing /admin/harness-releases page, per the explicit "do not
duplicate it" instruction.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio


class _Cursor:
    def __init__(self, items):
        self.items = list(items)

    def sort(self, key, direction=-1):
        self.items.sort(key=lambda d: d.get(key, 0), reverse=(direction == -1))
        return self

    def limit(self, n):
        self.items = self.items[:n]
        return self

    async def to_list(self, length=None):
        return list(self.items)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []

    def _match(self, d, filt):
        for k, v in (filt or {}).items():
            if d.get(k) != v:
                return False
        return True

    def find(self, filt=None, projection=None):
        matched = [dict(d) for d in self.docs if self._match(d, filt)]
        return _Cursor(matched)

    async def find_one(self, filt=None, projection=None, sort=None):
        matched = [d for d in self.docs if self._match(d, filt)]
        if sort:
            key, direction = sort[0]
            matched.sort(key=lambda d: d.get(key) or 0, reverse=(direction == -1))
        return dict(matched[0]) if matched else None

    async def count_documents(self, filt=None):
        return sum(1 for d in self.docs if self._match(d, filt))

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


class _FakeDB:
    def __init__(self):
        self.dev_users = _FakeColl()
        self.harness_telemetry = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


def _app_with_admin_router(db):
    from routers import admin_harness as ah_mod
    app = FastAPI()
    app.include_router(ah_mod.router, prefix="/api/aurem-dev")
    return app, ah_mod


async def _fake_admin(authorization=None):
    return {"user_id": "admin1", "is_admin": True}


class TestAdminHarnessSignups:
    async def test_lists_recent_signups_with_source_newest_first(self):
        db = _FakeDB()
        db.dev_users.docs = [
            {"email": "a@example.com", "created_at": 100, "signup_source": None},
            {"email": "b@example.com", "created_at": 300, "signup_source": "harness_homepage"},
            {"email": "c@example.com", "created_at": 200, "signup_source": None},
        ]
        app, ah_mod = _app_with_admin_router(db)
        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.get("/api/aurem-dev/admin/harness/signups")
            assert resp.status_code == 200
            body = resp.json()
            assert [r["email"] for r in body["signups"]] == [
                "b@example.com", "c@example.com", "a@example.com",
            ]
            assert body["harness_homepage_count"] == 1
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_filters_to_one_source_value(self):
        db = _FakeDB()
        db.dev_users.docs = [
            {"email": "a@example.com", "created_at": 100, "signup_source": None},
            {"email": "b@example.com", "created_at": 300, "signup_source": "harness_homepage"},
        ]
        app, ah_mod = _app_with_admin_router(db)
        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.get("/api/aurem-dev/admin/harness/signups?source=harness_homepage")
            body = resp.json()
            assert len(body["signups"]) == 1
            assert body["signups"][0]["email"] == "b@example.com"
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_non_admin_gets_403(self):
        db = _FakeDB()
        app, ah_mod = _app_with_admin_router(db)

        async def _deny(authorization=None):
            from fastapi import HTTPException
            raise HTTPException(403, "not admin")
        app.dependency_overrides[ah_mod.require_admin_dep] = _deny
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/aurem-dev/admin/harness/signups")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)


class TestAdminHarnessTelemetryStatus:
    async def test_reports_real_event_count_and_last_received(self):
        db = _FakeDB()
        now_a = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
        now_b = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
        db.harness_telemetry.docs = [
            {"user_id": "u1", "received_at": now_a},
            {"user_id": "u1", "received_at": now_b},
        ]
        app, ah_mod = _app_with_admin_router(db)
        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.get("/api/aurem-dev/admin/harness/telemetry-status")
            body = resp.json()
            assert body["total_events"] == 2
            assert body["last_received_at"] == now_b.isoformat()
            assert body["endpoint"] == "/api/harness/telemetry"
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_zero_events_reports_none_not_a_crash(self):
        db = _FakeDB()
        app, ah_mod = _app_with_admin_router(db)
        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.get("/api/aurem-dev/admin/harness/telemetry-status")
            body = resp.json()
            assert body["total_events"] == 0
            assert body["last_received_at"] is None
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
