"""FIX 3 / Admin Team S0 — regression lock test.

`GET /admin/users` and `GET /admin/users/{user_id}` must build their
response from an ALLOWLIST of `dev_users` fields, not an exclusion list.
Seeds a real row (real Preview Mongo, via MONGO_URL — same pattern as
tests/conftest.py's `_clear_signup_state_for_test_ips`) carrying every
forbidden field plus one field nobody has decided about yet, and asserts
none of them ever reach either admin response — while a known-safe field
(email, tier) still does, so an empty/broken response can't false-pass.
"""
import os
import time
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from server import app
from routers import admin_users as admin_users_router
from cto_services import auth as auth_module
from services.admin_user_fields import DEV_USER_FORBIDDEN_FIELDS

_TEST_USER_ID = "regtest-fix3-s0-allowlist-user"
_ADMIN_USER = {"user_id": "regtest-fix3-s0-owner", "email": "owner@regtest.local",
               "is_admin": True, "tier": "founder"}


async def _fake_admin_dep(*args, **kwargs):
    return _ADMIN_USER


async def _fake_admin_dep_no_args():
    return _ADMIN_USER


@pytest.fixture
def seeded_row():
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        pytest.skip("MONGO_URL/DB_NAME not configured in this environment")
    client = MongoClient(mongo_url, serverSelectionTimeoutMS=3000)
    db = client[db_name]
    doc = {
        "user_id": _TEST_USER_ID,
        "email": "fix3-s0-allowlist@regtest.local",
        "tier": "free",
        "created_at": time.time(),
        "password": "should-never-leak",
        "password_hash": "should-never-leak",
        "mfa_secret": "should-never-leak",
        "mfa_secret_pending": "should-never-leak",
        "mfa_backup_codes": ["should-never-leak"],
        "mfa_backup_codes_pending": ["should-never-leak"],
        "ora_pin_hash": "should-never-leak",
        "github": {"access_token": "should-never-leak"},
        # a field nobody has decided about yet — the allowlist must not
        # know it exists, by construction, not by a hand-added exclusion
        "totally_new_undocumented_field_2026": "should-never-leak",
    }
    db.dev_users.delete_one({"user_id": _TEST_USER_ID})
    db.dev_users.insert_one(doc)
    yield doc
    db.dev_users.delete_one({"user_id": _TEST_USER_ID})
    client.close()


def _assert_clean(payload: dict):
    blob = str(payload)
    for f in DEV_USER_FORBIDDEN_FIELDS:
        assert f not in payload, f"forbidden field {f!r} present as a top-level key"
        assert "should-never-leak" not in blob, "a forbidden field's value leaked into the response"
    assert "totally_new_undocumented_field_2026" not in payload
    assert "totally_new_undocumented_field_2026" not in blob


def test_regression_iter_adminteam_s0_list_users_allowlist_blocks_secrets(monkeypatch, seeded_row):
    monkeypatch.setattr(admin_users_router, "_require_admin", _fake_admin_dep)
    app.dependency_overrides[auth_module.require_admin_dep] = _fake_admin_dep_no_args
    try:
        with TestClient(app) as client:
            resp = client.get("/api/aurem-dev/admin/users", params={"search": "fix3-s0-allowlist"})
        assert resp.status_code == 200
        body = resp.json()
        users = body.get("users", [])
        assert len(users) >= 1, "seeded test row should be found by the search filter"
        row = next(u for u in users if u.get("user_id") == _TEST_USER_ID)
        _assert_clean(row)
        # a known-safe field must still be present — proves this is an
        # allowlist letting safe fields through, not a response that's
        # empty/broken and trivially "passing" by omitting everything
        assert row.get("email") == "fix3-s0-allowlist@regtest.local"
        assert row.get("tier") == "free"
    finally:
        app.dependency_overrides.pop(auth_module.require_admin_dep, None)


def test_regression_iter_adminteam_s0_get_user_allowlist_blocks_secrets(monkeypatch, seeded_row):
    monkeypatch.setattr(admin_users_router, "_require_admin", _fake_admin_dep)
    app.dependency_overrides[auth_module.require_admin_dep] = _fake_admin_dep_no_args
    try:
        with TestClient(app) as client:
            resp = client.get(f"/api/aurem-dev/admin/users/{_TEST_USER_ID}")
        assert resp.status_code == 200
        body = resp.json()
        _assert_clean(body)
        assert body.get("email") == "fix3-s0-allowlist@regtest.local"
        assert body.get("tier") == "free"
    finally:
        app.dependency_overrides.pop(auth_module.require_admin_dep, None)
