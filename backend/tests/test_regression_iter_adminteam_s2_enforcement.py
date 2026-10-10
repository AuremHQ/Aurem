"""Admin Team S2 (AT1b) — real-gate enforcement lock tests.

Every test here is `@pytest.mark.real_admin_gate` so it exercises the
ACTUAL `cto_services.auth._finish_admin_check` rule — not the
`tests/conftest.py` legacy-compat shim every other test in this suite
gets automatically (that shim exists ONLY so ~265 pre-existing tests
keep passing unedited; it must never apply here, or these tests would
prove nothing).

Real Preview Mongo + real JWTs via `create_token` + the REAL
`require_admin_dep` (no `dependency_overrides`) end-to-end through
`server.app`, same pattern as test_regression_iter_adminteam_s0/s1.
"""
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from server import app
from cto_services.auth import create_token

pytestmark = pytest.mark.real_admin_gate

_OWNER_EMAIL = "test@aurem.dev"  # on FOUNDER_EMAILS per backend/.env


@pytest.fixture(autouse=True)
def _ensure_real_async_db():
    """Several other test files in this suite call
    `cto_services.db.set_db(<a hand-rolled _FakeDB>)` — a process-global
    singleton — and some don't restore the real client afterward in
    every exit path. Harmless for ~265 pre-existing tests (they all go
    through the `_legacy_admin_bypass` short-circuit before ever
    touching the DB), but THESE tests deliberately opt out of that
    bypass (`real_admin_gate`) to exercise the real D12 DB-backed path,
    so they need a guaranteed-real connection regardless of what an
    earlier test in the same process left behind."""
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
        pytest.skip("MONGO_URL/DB_NAME not configured in this environment")
    client = MongoClient(mongo_url, serverSelectionTimeoutMS=3000)
    db = client[db_name]
    yield db
    client.close()


def _new_user_id(tag: str) -> str:
    return f"regtest-s2-{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def legacy_admin_user(mongo):
    """D1/D18 — is_admin=True AND is_founder=True AND tier=="founder",
    but the email is NOT on FOUNDER_EMAILS. Under the OLD rule this was
    an admin three times over; under S2 none of those flags matter."""
    uid = _new_user_id("legacy")
    doc = {
        "user_id": uid, "email": f"{uid}@regtest.local",
        "is_admin": True, "is_founder": True, "tier": "founder",
        "created_at": time.time(),
    }
    mongo.dev_users.insert_one(doc)
    yield doc
    mongo.dev_users.delete_one({"user_id": uid})


@pytest.fixture
def owner_row(mongo):
    row = mongo.dev_users.find_one({"email": _OWNER_EMAIL}, {"user_id": 1, "email": 1})
    if not row:
        pytest.skip(f"{_OWNER_EMAIL} must exist on this Preview for this test")
    return row


@pytest.fixture
def team_member_factory(mongo):
    """Creates a confirmed dev_users row (is_admin=False, D21) + an
    accepted admin_team_members row with the given overrides, and
    cleans both up afterward."""
    created = []

    def _make(overrides=None, role="developer", status="active"):
        uid = _new_user_id("teammate")
        email = f"{uid}@regtest.local"
        mongo.dev_users.insert_one({
            "user_id": uid, "email": email,
            "is_admin": False, "tier": "free",
            "created_at": time.time(),
        })
        mongo.admin_team_members.insert_one({
            "user_id": uid, "email": email, "role": role, "status": status,
            "overrides": overrides or {},
            "created_at": time.time(), "updated_at": time.time(),
        })
        created.append(uid)
        return uid, email

    yield _make
    for uid in created:
        mongo.dev_users.delete_one({"user_id": uid})
        mongo.admin_team_members.delete_one({"user_id": uid})


def _get(client, path, token):
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


def _post(client, path, token, json=None):
    return client.post(path, json=json or {}, headers={"Authorization": f"Bearer {token}"})


# ── D1/D14/D18 — Owner is ONLY a FOUNDER_EMAILS email ────────────────

def test_d1_legacy_is_admin_tier_founder_is_founder_grants_nothing(legacy_admin_user):
    """The exact drift D1 exists to close: is_admin=True + is_founder=True
    + tier=='founder', email NOT on the allow-list -> 403, not 200."""
    token = create_token(legacy_admin_user["user_id"], legacy_admin_user["email"], is_admin=True)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/users", token)
    assert resp.status_code == 403, (
        f"D1: is_admin/is_founder/tier=='founder' alone must grant nothing "
        f"post-S2, got {resp.status_code}: {resp.text[:200]}"
    )


def test_d1_owner_allowlist_email_full_access(owner_row):
    """The real Owner (FOUNDER_EMAILS) still gets in, including an
    owner-only route a team member could never reach."""
    token = create_token(owner_row["user_id"], owner_row["email"], is_admin=True)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/users", token)
        assert resp.status_code == 200
        # owner-only route (DELETE /admin/users/{id}) — use a bogus id,
        # we only care that the GATE lets the Owner past (404 "not
        # found" downstream proves the gate passed; 403 would prove it
        # didn't).
        resp2 = client.delete(
            "/api/aurem-dev/admin/users/regtest-s2-nonexistent-bogus-id",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp2.status_code != 403


# ── D12 — fail closed ─────────────────────────────────────────────────

def test_d12_no_dev_users_row_is_403_not_200():
    """A syntactically-valid JWT for a user_id with NO dev_users row at
    all must fail closed (403), never fall back to trusting the bare
    JWT is_admin claim (D12)."""
    ghost_uid = _new_user_id("ghost")
    token = create_token(ghost_uid, f"{ghost_uid}@regtest.local", is_admin=True)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/users", token)
    assert resp.status_code == 403, (
        f"D12: no confirmed dev_users row must be 403, got {resp.status_code}"
    )


# ── D21 — team members never need/receive is_admin=true ─────────────

def test_d21_team_member_dev_users_row_keeps_is_admin_false(team_member_factory, mongo):
    uid, email = team_member_factory(overrides={"users.all": "view"})
    row = mongo.dev_users.find_one({"user_id": uid})
    assert row["is_admin"] is False, "D21: a team member's dev_users row must never flip to is_admin=true"
    # And they still get real admin access via the registry, proving
    # is_admin=false does not mean "no access" once they're an active
    # member with a sufficient override + mfa claim.
    token = create_token(uid, email, is_admin=False, mfa_verified=True)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/users", token)
    assert resp.status_code == 200


# ── D6 — MFA session claim required for team members (not Owner) ────

def test_d6_team_member_without_mfa_claim_is_refused_when_opted_in(team_member_factory, monkeypatch):
    # Owner decision 2026-10: D6 is now opt-in (ADMIN_TEAM_REQUIRE_MFA=1).
    monkeypatch.setenv("ADMIN_TEAM_REQUIRE_MFA", "1")
    uid, email = team_member_factory(overrides={"users.all": "manage"})
    token = create_token(uid, email, is_admin=False, mfa_verified=False)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/users", token)
    assert resp.status_code == 403, "D6 (opted in): a team member token without the mfa claim must be refused"


def test_regression_iter417_team_member_without_mfa_claim_passes_by_default(team_member_factory, monkeypatch):
    monkeypatch.delenv("ADMIN_TEAM_REQUIRE_MFA", raising=False)
    uid, email = team_member_factory(overrides={"users.all": "manage"})
    token = create_token(uid, email, is_admin=False, mfa_verified=False)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/users", token)
    assert resp.status_code == 200, "a team member with the right screen override must not need a 2FA session"


def test_d6_team_member_with_mfa_claim_and_sufficient_level_passes(team_member_factory):
    uid, email = team_member_factory(overrides={"users.all": "view"})
    token = create_token(uid, email, is_admin=False, mfa_verified=True)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/users", token)
    assert resp.status_code == 200


def test_d6_pin_style_token_without_mfa_claim_still_works_for_owner(owner_row):
    """D6 — a PIN-minted-shaped token (is_admin=True, no mfa claim) must
    still work for the Owner specifically (PIN-login code is untouched,
    and the Owner check never requires the mfa claim)."""
    token = create_token(owner_row["user_id"], owner_row["email"], is_admin=True, mfa_verified=False)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/users", token)
    assert resp.status_code == 200


# ── Level enforcement (registry-driven) ──────────────────────────────

def test_level_enforcement_view_cannot_reach_manage_route(team_member_factory):
    """A team member with only VIEW on users.all must be refused on a
    route the registry marks MANAGE (suspend)."""
    uid, email = team_member_factory(overrides={"users.all": "view"})
    token = create_token(uid, email, is_admin=False, mfa_verified=True)
    with TestClient(app) as client:
        resp = _post(client, "/api/aurem-dev/admin/users/some-bogus-id/suspend", token)
    assert resp.status_code == 403


def test_level_enforcement_manage_override_reaches_manage_route(team_member_factory):
    """D3 — reconcile is Manage on money.payments; a team member with a
    Manage override on that exact screen gets past the gate (a 4xx/5xx
    from the handler's OWN business logic afterward is fine — we only
    assert the GATE didn't 403/401 them)."""
    uid, email = team_member_factory(overrides={"money.payments": "manage"})
    token = create_token(uid, email, is_admin=False, mfa_verified=True)
    with TestClient(app) as client:
        resp = _post(client, "/api/aurem-dev/admin/payments/reconcile", token)
    assert resp.status_code not in (401, 403)


def test_owner_only_route_refuses_even_a_manage_team_member(team_member_factory):
    """users/email-offer is OWNER_ONLY in the registry regardless of
    level — a team member with Manage on users.all must still be
    refused (D9)."""
    uid, email = team_member_factory(overrides={"users.all": "manage"})
    token = create_token(uid, email, is_admin=False, mfa_verified=True)
    with TestClient(app) as client:
        resp = _post(client, "/api/aurem-dev/admin/users/email-offer", token)
    assert resp.status_code == 403


def test_no_team_membership_at_all_is_refused():
    """A confirmed dev_users row (not a ghost) but NO admin_team_members
    row and not on the allow-list -> 403."""
    from pymongo import MongoClient as _MC
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        pytest.skip("MONGO_URL/DB_NAME not configured")
    db = _MC(mongo_url, serverSelectionTimeoutMS=3000)[db_name]
    uid = _new_user_id("plainuser")
    email = f"{uid}@regtest.local"
    db.dev_users.insert_one({"user_id": uid, "email": email, "is_admin": False, "created_at": time.time()})
    try:
        token = create_token(uid, email, is_admin=False, mfa_verified=True)
        with TestClient(app) as client:
            resp = _get(client, "/api/aurem-dev/admin/users", token)
        assert resp.status_code == 403
    finally:
        db.dev_users.delete_one({"user_id": uid})


# ── D13 — tier=founder is owner-only, plan-only (never access) ──────

def test_d13_team_member_cannot_set_tier_to_founder(team_member_factory):
    uid, email = team_member_factory(overrides={"users.plans": "manage"})
    token = create_token(uid, email, is_admin=False, mfa_verified=True)
    with TestClient(app) as client:
        resp = _post(
            client, f"/api/aurem-dev/admin/users/{uid}/tier", token,
            json={"tier": "founder"},
        )
    assert resp.status_code == 403, "D13: only the Owner may set tier=founder"


def test_d13_team_member_can_still_set_other_tiers(team_member_factory):
    uid, email = team_member_factory(overrides={"users.plans": "manage"})
    token = create_token(uid, email, is_admin=False, mfa_verified=True)
    with TestClient(app) as client:
        resp = _post(
            client, f"/api/aurem-dev/admin/users/{uid}/tier", token,
            json={"tier": "pro"},
        )
    assert resp.status_code == 200, f"D13 only restricts tier=founder, got {resp.status_code}: {resp.text[:200]}"


def test_d13_setting_tier_founder_does_not_grant_access(owner_row, team_member_factory, mongo):
    """D13's other half: even if tier gets forced to 'founder' directly
    in the DB (bypassing the owner-only check above), that alone must
    NOT grant admin access — only FOUNDER_EMAILS does (D1)."""
    uid, email = team_member_factory(overrides={})
    mongo.dev_users.update_one({"user_id": uid}, {"$set": {"tier": "founder"}})
    token = create_token(uid, email, is_admin=False, mfa_verified=True)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/users", token)
    assert resp.status_code == 403


# ── R4 — mfa.py self-service exemptions ──────────────────────────────

def test_r4_team_member_can_reach_2fa_status_without_mfa_claim(team_member_factory):
    uid, email = team_member_factory(overrides={})  # zero screen access
    token = create_token(uid, email, is_admin=False, mfa_verified=False)
    with TestClient(app) as client:
        resp = _get(client, "/api/aurem-dev/admin/2fa/status", token)
    assert resp.status_code == 200, (
        f"R4: status/enroll-* must be reachable without the mfa claim yet "
        f"so a team member can bootstrap 2FA, got {resp.status_code}: {resp.text[:200]}"
    )


def test_r4_team_member_disable_2fa_requires_mfa_claim(team_member_factory):
    uid, email = team_member_factory(overrides={})
    token = create_token(uid, email, is_admin=False, mfa_verified=False)
    with TestClient(app) as client:
        resp = _post(client, "/api/aurem-dev/admin/2fa/disable", token, json={"code": "000000"})
    assert resp.status_code == 403, "R4: disable must require an already-mfa-verified session"
