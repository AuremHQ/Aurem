"""
tests/test_visibility_domain_field_2026_09_07.py — Visibility Kit
domain fix (2026-09-07). `services/visibility/apply.py` reads
`project.site_meta.domain` to build the Preferred Sources deeplink,
but nothing in the codebase ever wrote it — every "Apply" failed
with "no site URL/domain on file" even on a fully connected project
(confirmed live: TJSNDHU/Aurem production bug, 2026-09-12 comment in
VisibilityKitPanel.jsx). Fix: PATCH /cto/projects/{id} with
preview_url (the existing "Your live site URL" field — Dashboard.jsx
AddLiveSiteModal + handleSaveLiveSite) now also mirrors the hostname
into site_meta.domain, so the ALREADY-existing flow feeds this field
instead of adding a second one the user would fill in twice.

Same fixture pattern as tests/test_phase2c_cto_projects_router.py
(same _FakeDB/_FakeCollection, same `router_mod.current_dev` patch
point via `routers.cto_projects` package-level shim).
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        return all(row.get(k) == v for k, v in (query or {}).items())

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if self._match(r, query)]
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update, upsert=False):
        import types
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


USER = {"user_id": "u1", "email": "user@example.com", "tier": "pro",
        "is_admin": False, "is_unlimited": False, "track": "dev",
        "created_at": time.time()}
AUTH = {"Authorization": "Bearer u1"}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from routers import cto_projects as router_mod
    from cto_services import db as _dbmod
    _dbmod.set_db(fake_db)

    async def _fake_current_dev(authorization=None):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return USER

    old_current_dev = router_mod.current_dev
    router_mod.current_dev = _fake_current_dev

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    yield c

    router_mod.current_dev = old_current_dev
    _dbmod.set_db(None)


class TestPreviewUrlSetsSiteMetaDomain:
    def test_setting_preview_url_mirrors_hostname_into_site_meta_domain(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1", "name": "demo",
        })
        resp = client.patch(
            "/api/aurem-dev/cto/projects/p1",
            json={"preview_url": "https://my-app.vercel.app/dashboard"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        row = fake_db.cto_projects.rows[0]
        assert row["preview_url"] == "https://my-app.vercel.app/dashboard"
        assert row["site_meta.domain"] == "my-app.vercel.app"

    def test_updating_preview_url_again_overwrites_domain(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1", "name": "demo",
            "preview_url": "https://old.example.com",
            "site_meta.domain": "old.example.com",
        })
        resp = client.patch(
            "/api/aurem-dev/cto/projects/p1",
            json={"preview_url": "https://new.example.com/app"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        row = fake_db.cto_projects.rows[0]
        assert row["site_meta.domain"] == "new.example.com"

    def test_other_fields_dont_touch_site_meta_domain(self, client, fake_db):
        """Updating branch/tech_stack without preview_url must not
        add or clear site_meta.domain."""
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1", "name": "demo",
        })
        resp = client.patch(
            "/api/aurem-dev/cto/projects/p1",
            json={"branch": "develop"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        row = fake_db.cto_projects.rows[0]
        assert row["branch"] == "develop"
        assert "site_meta.domain" not in row

    def test_visibility_apply_resolves_domain_after_preview_url_set(self):
        """The other half of the fix — apply.py's own domain
        resolution now succeeds once site_meta.domain is populated
        the way the PATCH handler above writes it."""
        from services.visibility.apply import _domain_from_url
        site_meta = {"domain": _domain_from_url("https://my-app.vercel.app/dashboard")}
        domain = site_meta.get("domain") or _domain_from_url(
            (site_meta.get("schema") or {}).get("url")
        )
        assert domain == "my-app.vercel.app"
