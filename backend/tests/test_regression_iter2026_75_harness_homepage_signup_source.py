"""tests/test_regression_iter2026_75_harness_homepage_signup_source.py

Launch-copy-fix follow-up (2026-11-16) — Task 2's "record the source"
requirement: a signup originating from the /harness page's own flow
(Signup.jsx tagging `source: "harness_homepage"` when reached via
`?next=/harness`) must be stored on the dev_users row as
`signup_source`, using the SAME real ORA account system as every
other signup — no new auth system, no separate user table.

Also locks in the closed-set provenance discipline: an unrecognized
`source` value is dropped (stored as None), never persisted as free
text, matching services/harness_releases.py's own _VALID_SOURCES
pattern.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []

    async def find_one(self, filt=None, projection=None):
        filt = filt or {}
        for d in self.docs:
            ok = True
            for k, v in filt.items():
                if isinstance(v, dict):
                    continue  # e.g. _email_ci's $regex — not modeled here
                if d.get(k) != v:
                    ok = False
                    break
            if ok:
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, filt, update, upsert=False):
        return None


class _FakeDB:
    def __init__(self):
        self.dev_users = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


def _app_with_auth_router(db):
    from routers import auth as auth_mod
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/aurem-dev")
    return app, auth_mod


def _patched(auth_mod, db):
    return (
        patch.object(auth_mod, "get_db", lambda: db),
        patch("services.signup_guards.enforce_signup_guards", AsyncMock(return_value=None)),
        patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)),
        patch("services.verification_email.send_verification_email", AsyncMock(return_value=None)),
    )


class TestHarnessHomepageSignupSource:
    async def test_harness_homepage_source_is_persisted(self):
        db = _FakeDB()
        app, auth_mod = _app_with_auth_router(db)
        p1, p2, p3, p4 = _patched(auth_mod, db)
        with p1, p2, p3, p4:
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/auth/signup", json={
                "email": "harness-signup@example.com",
                "password": "TestPass123!",
                "source": "harness_homepage",
            })
        assert resp.status_code == 200, resp.text
        assert db.dev_users.docs[0]["signup_source"] == "harness_homepage"

    async def test_no_source_field_stores_none_regression(self):
        """Existing behavior: a payload with no `source` key at all
        (every signup path that predates this field) still works and
        stores signup_source=None."""
        db = _FakeDB()
        app, auth_mod = _app_with_auth_router(db)
        p1, p2, p3, p4 = _patched(auth_mod, db)
        with p1, p2, p3, p4:
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/auth/signup", json={
                "email": "plain-signup@example.com",
                "password": "TestPass123!",
            })
        assert resp.status_code == 200, resp.text
        assert db.dev_users.docs[0]["signup_source"] is None

    async def test_unrecognized_source_value_is_dropped_not_stored_as_free_text(self):
        db = _FakeDB()
        app, auth_mod = _app_with_auth_router(db)
        p1, p2, p3, p4 = _patched(auth_mod, db)
        with p1, p2, p3, p4:
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/auth/signup", json={
                "email": "spoofed-source@example.com",
                "password": "TestPass123!",
                "source": "<script>whatever</script>",
            })
        assert resp.status_code == 200, resp.text
        assert db.dev_users.docs[0]["signup_source"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
