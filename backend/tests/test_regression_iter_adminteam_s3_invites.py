"""Admin Team S3 (AT2) — Team screen + invite flow lock tests.

Real Preview Mongo + real JWTs + real server.app end-to-end, same
pattern as the S0/S1/S2 regression files. `@pytest.mark.real_admin_gate`
since these exercise the real gate (owner-only team management).
"""
import hashlib
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from server import app
from cto_services.auth import create_token

pytestmark = pytest.mark.real_admin_gate

_OWNER_EMAIL = "test@aurem.dev"


@pytest.fixture(autouse=True)
def _ensure_real_async_db():
    """See test_regression_iter_adminteam_s2_enforcement.py — guards
    against this suite's pre-existing cross-test _FakeDB/set_db() leak."""
    import cto_services.db as _dbmod
    from motor.motor_asyncio import AsyncIOMotorClient
    prior = _dbmod.get_db()
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if mongo_url and db_name:
        _dbmod.set_db(AsyncIOMotorClient(mongo_url)[db_name])
    yield
    _dbmod.set_db(prior)


@pytest.fixture
def mongo():
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        pytest.skip("MONGO_URL/DB_NAME not configured")
    client = MongoClient(mongo_url, serverSelectionTimeoutMS=3000)
    db = client[db_name]
    yield db
    client.close()


def _new_user_id(tag: str) -> str:
    return f"regtest-s3-{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def owner_token(mongo):
    row = mongo.dev_users.find_one({"email": _OWNER_EMAIL}, {"user_id": 1, "email": 1})
    if not row:
        pytest.skip(f"{_OWNER_EMAIL} must exist on this Preview for this test")
    return create_token(row["user_id"], row["email"], is_admin=True), row


@pytest.fixture
def invitee(mongo):
    """A real dev_users row for the person being invited, so they can
    actually log in and accept."""
    uid = _new_user_id("invitee")
    email = f"{uid}@regtest.local"
    mongo.dev_users.insert_one({
        "user_id": uid, "email": email, "is_admin": False,
        "tier": "free", "created_at": time.time(),
    })
    yield uid, email
    mongo.dev_users.delete_one({"user_id": uid})
    mongo.admin_team_members.delete_one({"user_id": uid})


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def test_owner_can_create_list_and_cancel_invite(owner_token, mongo, invitee):
    token, owner_row = owner_token
    _, email = invitee
    with TestClient(app) as client:
        resp = client.post(
            "/api/aurem-dev/admin/team/invites", headers=_hdr(token),
            json={"email": email, "role": "developer"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        raw_token = data["token"]
        invite_id = data["invite_id"]
        assert len(raw_token) > 20

        # D8 — only the hash is stored, never the raw token.
        stored = mongo.admin_team_invites.find_one({"invite_id": invite_id})
        assert stored["token_hash"] == hashlib.sha256(raw_token.encode()).hexdigest()
        assert "token" not in stored

        # Listing never exposes token_hash either.
        resp2 = client.get("/api/aurem-dev/admin/team/invites", headers=_hdr(token))
        assert resp2.status_code == 200
        listed = [i for i in resp2.json()["invites"] if i["invite_id"] == invite_id]
        assert len(listed) == 1
        assert "token_hash" not in listed[0]
        assert "token" not in listed[0]

        resp3 = client.post(f"/api/aurem-dev/admin/team/invites/{invite_id}/cancel", headers=_hdr(token))
        assert resp3.status_code == 200
    mongo.admin_team_invites.delete_one({"invite_id": invite_id})


def test_non_owner_cannot_manage_team(invitee):
    """A plain logged-in user (not Owner, not a team member) must be
    refused by every admin/team/* management route (D9 — owner-only)."""
    uid, email = invitee
    token = create_token(uid, email, is_admin=False)
    with TestClient(app) as client:
        resp = client.get("/api/aurem-dev/admin/team/members", headers=_hdr(token))
    assert resp.status_code == 403


def test_full_invite_accept_flow_creates_active_member_and_notifies_owner(owner_token, mongo, invitee):
    token, owner_row = owner_token
    uid, email = invitee

    with TestClient(app) as client:
        create_resp = client.post(
            "/api/aurem-dev/admin/team/invites", headers=_hdr(token),
            json={"email": email, "role": "developer"},
        )
        assert create_resp.status_code == 200
        raw_token = create_resp.json()["token"]
        invite_id = create_resp.json()["invite_id"]

        invitee_token = create_token(uid, email, is_admin=False)
        accept_resp = client.post(
            "/api/aurem-dev/team-invite/accept", headers=_hdr(invitee_token),
            json={"token": raw_token},
        )
        assert accept_resp.status_code == 200, accept_resp.text
        body = accept_resp.json()
        assert body["ok"] is True
        # D6/spec — acceptance never signs them in or returns a session.
        assert "token" not in body and "access_token" not in body

        member = mongo.admin_team_members.find_one({"user_id": uid})
        assert member is not None
        assert member["status"] == "active"
        assert member["role"] == "developer"
        assert member["email"] == email

        # D21 — accepting an invite never flips dev_users.is_admin.
        row = mongo.dev_users.find_one({"user_id": uid})
        assert row["is_admin"] is False

        invite_row = mongo.admin_team_invites.find_one({"invite_id": invite_id})
        assert invite_row["status"] == "accepted"
        assert invite_row["accepted_by"] == uid

        # Owner gets a bell notification.
        notif = mongo.notifications.find_one({
            "user_id": owner_row["user_id"], "type": "admin_message",
            "text": {"$regex": email},
        })
        assert notif is not None

        # Re-using the SAME raw token again must now fail (single use).
        replay_resp = client.post(
            "/api/aurem-dev/team-invite/accept", headers=_hdr(invitee_token),
            json={"token": raw_token},
        )
        assert replay_resp.status_code == 400
    mongo.admin_team_invites.delete_one({"invite_id": invite_id})
    mongo.notifications.delete_many({"user_id": owner_row["user_id"], "text": {"$regex": email}})


def test_accept_wrong_email_is_refused(owner_token, mongo, invitee):
    token, _owner_row = owner_token
    uid, email = invitee
    other_uid = _new_user_id("other")
    other_email = f"{other_uid}@regtest.local"
    mongo.dev_users.insert_one({"user_id": other_uid, "email": other_email, "is_admin": False, "created_at": time.time()})
    try:
        with TestClient(app) as client:
            create_resp = client.post(
                "/api/aurem-dev/admin/team/invites", headers=_hdr(token),
                json={"email": email, "role": "developer"},
            )
            raw_token = create_resp.json()["token"]
            invite_id = create_resp.json()["invite_id"]
            other_token = create_token(other_uid, other_email, is_admin=False)
            resp = client.post(
                "/api/aurem-dev/team-invite/accept", headers=_hdr(other_token),
                json={"token": raw_token},
            )
            assert resp.status_code == 403
        mongo.admin_team_invites.delete_one({"invite_id": invite_id})
    finally:
        mongo.dev_users.delete_one({"user_id": other_uid})


def test_copy_link_rotates_old_token(owner_token, mongo, invitee):
    token, _owner_row = owner_token
    uid, email = invitee
    with TestClient(app) as client:
        create_resp = client.post(
            "/api/aurem-dev/admin/team/invites", headers=_hdr(token),
            json={"email": email, "role": "developer"},
        )
        old_token = create_resp.json()["token"]
        invite_id = create_resp.json()["invite_id"]

        regen_resp = client.post(f"/api/aurem-dev/admin/team/invites/{invite_id}/copy-link", headers=_hdr(token))
        assert regen_resp.status_code == 200
        new_token = regen_resp.json()["token"]
        assert new_token != old_token

        invitee_token = create_token(uid, email, is_admin=False)
        old_resp = client.post(
            "/api/aurem-dev/team-invite/accept", headers=_hdr(invitee_token),
            json={"token": old_token},
        )
        assert old_resp.status_code == 400, "D8: the OLD token must no longer work after copy-link"

        new_resp = client.post(
            "/api/aurem-dev/team-invite/accept", headers=_hdr(invitee_token),
            json={"token": new_token},
        )
        assert new_resp.status_code == 200
    mongo.admin_team_invites.delete_one({"invite_id": invite_id})


def test_suspend_then_resume_cycle(owner_token, mongo, invitee):
    token, _owner_row = owner_token
    uid, email = invitee
    mongo.admin_team_members.insert_one({
        "user_id": uid, "email": email, "role": "developer", "status": "active",
        "overrides": {}, "created_at": time.time(), "updated_at": time.time(),
    })
    with TestClient(app) as client:
        s = client.post(f"/api/aurem-dev/admin/team/members/{uid}/suspend", headers=_hdr(token))
        assert s.status_code == 200
        suspended_token = create_token(uid, email, is_admin=False, mfa_verified=True)
        blocked = client.get("/api/aurem-dev/admin/team/members", headers=_hdr(suspended_token))
        assert blocked.status_code == 403, "a suspended member must not pass as an active member anywhere"

        r = client.post(f"/api/aurem-dev/admin/team/members/{uid}/resume", headers=_hdr(token))
        assert r.status_code == 200
        row = mongo.admin_team_members.find_one({"user_id": uid})
        assert row["status"] == "active"


def test_overrides_validate_level_and_persist(owner_token, mongo, invitee):
    token, _owner_row = owner_token
    uid, email = invitee
    mongo.admin_team_members.insert_one({
        "user_id": uid, "email": email, "role": "developer", "status": "active",
        "overrides": {}, "created_at": time.time(), "updated_at": time.time(),
    })
    with TestClient(app) as client:
        bad = client.patch(
            f"/api/aurem-dev/admin/team/members/{uid}/overrides", headers=_hdr(token),
            json={"overrides": {"users.all": "superadmin"}},
        )
        assert bad.status_code == 400

        good = client.patch(
            f"/api/aurem-dev/admin/team/members/{uid}/overrides", headers=_hdr(token),
            json={"overrides": {"users.all": "view"}},
        )
        assert good.status_code == 200
        row = mongo.admin_team_members.find_one({"user_id": uid})
        assert row["overrides"] == {"users.all": "view"}
