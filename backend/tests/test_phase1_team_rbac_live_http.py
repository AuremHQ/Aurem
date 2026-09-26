"""
test_phase1_team_rbac_live_http.py — 2026-02 · Phase 1 (Team Roles & Permissions)

REAL end-to-end HTTP tests against the live running backend (no
mocks): signs up a fresh owner account, invites a fresh team member,
accepts the invite (via the real accept-invite endpoint, token read
directly from Mongo to avoid depending on outbound email delivery in
CI), and proves the permission gate is enforced by the SERVER, not
the UI:
  - A Viewer's write attempt gets a REAL 403 from a real request.
  - A Viewer's read succeeds and is scoped to the owner's shared data.
  - Elevating the same member to write access makes the SAME
    previously-403'd request succeed.
  - Revoking the member removes their access to the owner's data.
  - The owner role cannot be edited/revoked (no team_members row for
    it exists at all).
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass
API = f"{BASE_URL}/api/aurem-dev" if BASE_URL else None

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")


@pytest.fixture(scope="module")
def db():
    if not MONGO_URL or not DB_NAME:
        pytest.skip("No MONGO_URL/DB_NAME")
    client = MongoClient(MONGO_URL)
    return client[DB_NAME]


@pytest.fixture(scope="module")
def owner():
    if not API:
        pytest.skip("No REACT_APP_BACKEND_URL")
    email = f"rbac-owner-{uuid.uuid4().hex[:10]}@example.com"
    password = "RbacTest2026!"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password, "name": "RBAC Owner Test",
    }, timeout=15)
    assert r.status_code == 200, f"owner signup failed: {r.status_code} {r.text}"
    token = r.json()["token"]
    me = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=10).json()
    return {"email": email, "token": token, "user_id": me["user"]["user_id"] if "user" in me else me.get("user_id")}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_owner_has_full_access_with_no_team_members_row(owner, db):
    r = requests.get(f"{API}/team/me", headers=_auth(owner["token"]), timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["is_owner"] is True
    assert body["role_label"] == "owner"
    assert all(v == "read_write" for v in body["permissions"].values())
    # By construction: the owner is never a team_members row.
    row = db.team_members.find_one({"owner_id": owner["user_id"], "role_label": "owner"})
    assert row is None


def test_invite_defaults_to_least_privilege_and_viewer_write_is_a_real_403(owner, db):
    invitee_email = f"rbac-viewer-{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(f"{API}/team/invite", json={
        "email": invitee_email, "role_label": "viewer",
    }, headers=_auth(owner["token"]), timeout=15)
    assert r.status_code == 200, r.text
    member_id = r.json()["member_id"]

    row = db.team_members.find_one({"member_id": member_id})
    assert row is not None
    assert row["status"] == "pending"
    assert row["permissions"]["domain"] == "read"       # viewer preset
    assert row["permissions"]["team_management"] == "none"

    # Accept the invite for real, via the real endpoint — token read
    # from Mongo only to avoid depending on outbound email in CI.
    invite_token = row["invite_token"]
    r = requests.post(f"{API}/team/accept-invite", json={
        "token": invite_token, "password": "ViewerTest2026!",
    }, timeout=15)
    assert r.status_code == 200, r.text
    viewer_token = r.json()["token"]

    # First set a REAL domain config on the OWNER's account so there is
    # real shared data to observe (or fail to touch).
    r = requests.post(f"{API}/domain/config", json={
        "domain": "rbac-e2e-test.example.com", "server_ip": "203.0.113.10",
    }, headers=_auth(owner["token"]), timeout=15)
    assert r.status_code == 200, r.text

    # Viewer CAN read the owner's real (shared) domain config.
    r = requests.get(f"{API}/domain/config", headers=_auth(viewer_token), timeout=10)
    assert r.status_code == 200
    assert r.json()["domain"] == "rbac-e2e-test.example.com"

    # Viewer CANNOT write — real 403 from a real request, not mocked.
    r = requests.post(f"{API}/domain/config", json={
        "domain": "should-be-blocked.example.com", "server_ip": "203.0.113.20",
    }, headers=_auth(viewer_token), timeout=15)
    assert r.status_code == 403
    detail = r.json().get("detail", {})
    assert detail.get("error") == "permission_denied"
    assert detail.get("resource") == "domain"
    assert detail.get("required") == "write"

    # Confirm the blocked write really did not change anything.
    r = requests.get(f"{API}/domain/config", headers=_auth(owner["token"]), timeout=10)
    assert r.json()["domain"] == "rbac-e2e-test.example.com"


def test_elevating_permission_makes_the_same_call_succeed(owner, db):
    # Re-invite + accept fresh (tests run independently / any order).
    invitee_email = f"rbac-editor-{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(f"{API}/team/invite", json={
        "email": invitee_email, "role_label": "viewer",
    }, headers=_auth(owner["token"]), timeout=15)
    member_id = r.json()["member_id"]
    row = db.team_members.find_one({"member_id": member_id})
    r = requests.post(f"{API}/team/accept-invite", json={
        "token": row["invite_token"], "password": "EditorTest2026!",
    }, timeout=15)
    member_token = r.json()["token"]

    # Denied before the grant.
    r = requests.post(f"{API}/domain/config", json={
        "domain": "still-blocked.example.com", "server_ip": "203.0.113.30",
    }, headers=_auth(member_token), timeout=15)
    assert r.status_code == 403

    # Owner grants a CUSTOM per-resource override (write on domain).
    r = requests.put(f"{API}/team/members/{member_id}/permissions", json={
        "permissions": {"domain": "write"},
    }, headers=_auth(owner["token"]), timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["permissions"]["domain"] == "write"

    # Audit log recorded the change.
    audit = db.permission_audit_log.find_one({
        "target_member_id": member_id, "action": "update_permissions",
    })
    assert audit is not None
    assert audit["new_value"]["permissions"]["domain"] == "write"

    # SAME call, now succeeds — proves the gate is live, not cached.
    r = requests.post(f"{API}/domain/config", json={
        "domain": "now-allowed.example.com", "server_ip": "203.0.113.40",
    }, headers=_auth(member_token), timeout=15)
    assert r.status_code == 200, r.text

    r = requests.get(f"{API}/domain/config", headers=_auth(owner["token"]), timeout=10)
    assert r.json()["domain"] == "now-allowed.example.com"


def test_revoke_removes_access_to_the_owners_data(owner, db):
    invitee_email = f"rbac-revoke-{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(f"{API}/team/invite", json={
        "email": invitee_email, "role_label": "admin",
    }, headers=_auth(owner["token"]), timeout=15)
    member_id = r.json()["member_id"]
    row = db.team_members.find_one({"member_id": member_id})
    r = requests.post(f"{API}/team/accept-invite", json={
        "token": row["invite_token"], "password": "RevokeTest2026!",
    }, timeout=15)
    member_token = r.json()["token"]

    # Admin can read the owner's shared data before revocation.
    r = requests.get(f"{API}/team/me", headers=_auth(member_token), timeout=10)
    assert r.json()["account_id"] == owner["user_id"]

    r = requests.post(f"{API}/team/members/{member_id}/revoke",
                       headers=_auth(owner["token"]), timeout=15)
    assert r.status_code == 200

    # Same JWT, but the team_members row is now "revoked" — resolve_context
    # no longer finds an ACTIVE row, so they fall back to being the owner
    # of their OWN (separate, empty) account — access to the real owner's
    # data is gone.
    r = requests.get(f"{API}/team/me", headers=_auth(member_token), timeout=10)
    assert r.status_code == 200
    assert r.json()["account_id"] != owner["user_id"]


def test_owner_row_cannot_be_touched_via_the_management_api(owner, db):
    # There's no member_id for the owner — any attempt to revoke/edit
    # "the owner" via a fabricated id 404s, it is simply not reachable.
    fake_id = owner["user_id"]
    r = requests.post(f"{API}/team/members/{fake_id}/revoke",
                       headers=_auth(owner["token"]), timeout=15)
    assert r.status_code == 404


def test_unauthenticated_request_to_a_gated_endpoint_is_rejected(owner):
    r = requests.get(f"{API}/domain/config", timeout=10)
    assert r.status_code == 401
