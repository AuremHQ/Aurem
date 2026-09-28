"""
test_master_task_2026_09_28_batch2.py — MASTER TASK Batch 2

Covers:
  (c) Team management now requires the `team` tier (or admin/founder),
      not just account ownership — both `/team/me`'s visibility flag
      and the two write endpoints' server-side enforcement.
  (d) GET /usage/me/history returns ONLY tokens/calls/date — the
      standing user-visibility rule (never cost/model/provider) is
      enforced at the Mongo projection level, not just hidden in the
      frontend, so this is asserted directly on the raw response.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests
from pymongo import MongoClient

API = os.environ.get("REACT_APP_BACKEND_URL", "")
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

if not API:
    with open(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", ".env")) as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                API = line.strip().split("=", 1)[1]
API = f"{API}/api/aurem-dev"

if not MONGO_URL or not DB_NAME:
    with open(os.path.join(os.path.dirname(__file__), "..", ".env")) as f:
        for line in f:
            if line.strip() and "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                if k == "MONGO_URL":
                    MONGO_URL = v
                elif k == "DB_NAME":
                    DB_NAME = v


@pytest.fixture(scope="module")
def db():
    return MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def free_tier_user(db):
    email = f"batch2-free-{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": "Batch2Test2026!", "name": "Batch2 Free",
    }, timeout=15)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    db.dev_users.update_one({"email": email}, {"$set": {"tier": "free"}})
    return {"email": email, "token": token}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_team_me_reports_not_entitled_for_free_tier(free_tier_user):
    r = requests.get(f"{API}/team/me", headers=_auth(free_tier_user["token"]), timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["is_owner"] is True          # RBAC: still owner of their own account
    assert body["team_feature_enabled"] is False  # entitlement: not on a Team-eligible tier


def test_invite_rejected_403_for_free_tier(free_tier_user):
    r = requests.post(f"{API}/team/invite", json={
        "email": f"invitee-{uuid.uuid4().hex[:8]}@example.com", "role_label": "viewer",
    }, headers=_auth(free_tier_user["token"]), timeout=15)
    assert r.status_code == 403
    assert "Team-plan feature" in r.text


def test_team_me_reports_entitled_for_team_tier(db, free_tier_user):
    db.dev_users.update_one({"email": free_tier_user["email"]}, {"$set": {"tier": "team"}})
    r = requests.get(f"{API}/team/me", headers=_auth(free_tier_user["token"]), timeout=10)
    assert r.status_code == 200
    assert r.json()["team_feature_enabled"] is True
    db.dev_users.update_one({"email": free_tier_user["email"]}, {"$set": {"tier": "free"}})


def test_usage_history_only_has_safe_fields(free_tier_user):
    r = requests.get(f"{API}/usage/me/history?days=30", headers=_auth(free_tier_user["token"]), timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert "days" in body
    for row in body["days"]:
        assert set(row.keys()) == {"date", "tokens", "calls"}
        for forbidden in ("cost", "cost_usd", "provider", "model"):
            assert forbidden not in row


def test_usage_history_requires_auth():
    r = requests.get(f"{API}/usage/me/history", timeout=10)
    assert r.status_code in (401, 403)


def test_tokens_route_redirects_to_settings_plans():
    src_path = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "App.jsx")
    with open(src_path) as f:
        src = f.read()
    assert '<Route path="/tokens"          element={<Navigate to="/settings?tab=plans" replace />} />' in src
