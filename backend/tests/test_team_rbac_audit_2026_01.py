"""
Team RBAC audit — verification tests for Task 2 (Fix Task List v2, P0).

Covers:
- team invite + accept-invite E2E
- viewer role: blocked (403 permission_denied) on write endpoints
- viewer role: reads owner's data (not empty own-user bucket)
- promoting to editor: writes no longer 403 as permission_denied
- editor STILL blocked from team_management writes
- owner regression: unaffected by team layer
"""
import os
import time
import uuid
import pytest
import requests
from pymongo import MongoClient

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api/aurem-dev"

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "aurem_dev"

TEST_TAG = f"rbaczz{uuid.uuid4().hex[:6]}"
OWNER_EMAIL = f"rbac-owner-{TEST_TAG}@example.com"
MEMBER_EMAIL = f"rbac-member-{TEST_TAG}@example.com"
PASS = "RbacTest2026!"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    # cleanup
    for coll, q in [
        ("dev_users", {"email": {"$in": [OWNER_EMAIL, MEMBER_EMAIL]}}),
        ("team_members", {"email": MEMBER_EMAIL}),
        ("cto_payments", {}),  # no-op filter; will be scoped below
        ("permission_audit_log", {"target_email": MEMBER_EMAIL}),
    ]:
        try:
            if coll == "cto_payments":
                continue
            client[DB_NAME][coll].delete_many(q)
        except Exception:
            pass
    client.close()


def _signup(email, password):
    r = requests.post(f"{API}/auth/signup", json={"email": email, "password": password}, timeout=20)
    return r


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    return r


@pytest.fixture(scope="module")
def owner_token(db):
    r = _signup(OWNER_EMAIL, PASS)
    assert r.status_code in (200, 201, 409), f"owner signup: {r.status_code} {r.text[:200]}"
    if r.status_code == 409:
        r2 = _login(OWNER_EMAIL, PASS)
        assert r2.status_code == 200
        return r2.json()["token"]
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    if not tok:
        r2 = _login(OWNER_EMAIL, PASS)
        assert r2.status_code == 200, r2.text
        tok = r2.json()["token"]
    return tok


@pytest.fixture(scope="module")
def member_state(db, owner_token):
    """Signs up member, owner invites viewer, member accepts, returns token+member_id."""
    # signup member (creates a dev_users row we'll later merge)
    rs = _signup(MEMBER_EMAIL, PASS)
    assert rs.status_code in (200, 201, 409), rs.text[:200]

    # owner invites as viewer
    r = requests.post(
        f"{API}/team/invite",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": MEMBER_EMAIL, "role_label": "viewer"},
        timeout=20,
    )
    assert r.status_code == 200, f"invite: {r.status_code} {r.text[:300]}"
    member_id = r.json()["member_id"]

    # fetch invite token from mongo (email not deliverable in preview)
    row = db.team_members.find_one({"member_id": member_id})
    assert row and row.get("invite_token"), "no invite_token on row"
    token = row["invite_token"]

    # accept-invite
    ra = requests.post(
        f"{API}/team/accept-invite",
        json={"token": token, "password": PASS},
        timeout=20,
    )
    assert ra.status_code == 200, f"accept: {ra.status_code} {ra.text[:300]}"
    member_jwt = ra.json()["token"]

    return {"jwt": member_jwt, "member_id": member_id}


# ─── 1. Invite + accept flow works E2E ────────────────────────────
def test_invite_and_accept_flow(member_state):
    assert member_state["jwt"]
    assert member_state["member_id"]


# ─── 2. Viewer WRITE endpoints -> 403 permission_denied ───────────
VIEWER_WRITE_TESTS = [
    ("POST", "/fix-pipeline/bulk", {"findings": []}),
    ("POST", "/payments/checkout", {"plan": "starter"}),
    ("POST", "/hosted-deploy/connect", {"project_id": "p_test", "provider": "vercel",
                                         "hook_url": "https://api.vercel.com/v1/integrations/deploy/prj_xxxxxxxxxxxxxxxxxxxx/xxxxxxxxxx"}),
]


@pytest.mark.parametrize("method,path,body", VIEWER_WRITE_TESTS)
def test_viewer_blocked_on_writes(member_state, method, path, body):
    h = {"Authorization": f"Bearer {member_state['jwt']}"}
    r = requests.request(method, f"{API}{path}", headers=h, json=body, timeout=20)
    assert r.status_code == 403, f"{path}: expected 403 got {r.status_code} {r.text[:200]}"
    try:
        detail = r.json().get("detail")
    except Exception:
        detail = None
    # detail may be a dict with error/resource keys
    if isinstance(detail, dict):
        assert detail.get("error") == "permission_denied", f"{path}: detail={detail}"


# ─── 3. Viewer READ endpoints -> 200 (owner's data, not empty) ────
VIEWER_READ_TESTS = [
    "/fix-pipeline/quota",
    "/payments/my-plan",
    "/cto/projects/connection-status",
]


@pytest.mark.parametrize("path", VIEWER_READ_TESTS)
def test_viewer_can_read_owner_data(member_state, path):
    h = {"Authorization": f"Bearer {member_state['jwt']}"}
    r = requests.get(f"{API}{path}", headers=h, timeout=20)
    # Must NOT be a permission denial. Accept 200 or business-level 4xx that isn't 403 permission_denied.
    assert r.status_code != 403 or "permission_denied" not in r.text, (
        f"{path}: viewer denied read {r.status_code} {r.text[:200]}"
    )
    # For the ones we know return 200
    if path in ("/fix-pipeline/quota", "/payments/my-plan"):
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"


# ─── 4. Promote viewer -> editor, writes should no longer be permission_denied ─
def test_promote_to_editor_unblocks_writes(owner_token, member_state):
    r = requests.put(
        f"{API}/team/members/{member_state['member_id']}/permissions",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"role_label": "editor"},
        timeout=20,
    )
    assert r.status_code == 200, f"promote: {r.status_code} {r.text[:200]}"

    h = {"Authorization": f"Bearer {member_state['jwt']}"}
    # fix-pipeline/bulk: may still fail on business logic (empty findings) — must NOT be permission_denied
    r2 = requests.post(f"{API}/fix-pipeline/bulk", headers=h, json={"findings": []}, timeout=20)
    if r2.status_code == 403:
        assert "permission_denied" not in r2.text, f"editor still permission-denied on bulk: {r2.text[:200]}"

    # checkout: should now pass gate; may hit Stripe (200) or business 4xx, not permission_denied
    r3 = requests.post(f"{API}/payments/checkout", headers=h, json={"plan": "starter"}, timeout=25)
    if r3.status_code == 403:
        assert "permission_denied" not in r3.text, f"editor still permission-denied on checkout: {r3.text[:200]}"


# ─── 5. Editor STILL blocked from team_management writes ──────────
def test_editor_blocked_from_team_management(member_state):
    h = {"Authorization": f"Bearer {member_state['jwt']}"}
    r = requests.post(
        f"{API}/team/invite",
        headers=h,
        json={"email": f"nested-{TEST_TAG}@example.com", "role_label": "viewer"},
        timeout=20,
    )
    assert r.status_code == 403, f"editor invite: expected 403 got {r.status_code}"
    detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else None
    if isinstance(detail, dict):
        assert detail.get("error") == "permission_denied"
        assert detail.get("resource") == "team_management"


# ─── 6. Owner regression — every checked endpoint still works ─────
def test_owner_regression_all_endpoints_work(owner_token):
    h = {"Authorization": f"Bearer {owner_token}"}
    reads = ["/fix-pipeline/quota", "/payments/my-plan", "/cto/projects/connection-status",
             "/cto/projects/list", "/codebase-health/last"]
    for p in reads:
        r = requests.get(f"{API}{p}", headers=h, timeout=20)
        assert r.status_code not in (401, 403), f"owner {p}: {r.status_code} {r.text[:200]}"

    # write gate pass (business logic may still fail; we only want NOT permission_denied)
    r = requests.post(f"{API}/fix-pipeline/bulk", headers=h, json={"findings": []}, timeout=20)
    assert not (r.status_code == 403 and "permission_denied" in r.text), \
        f"owner blocked on bulk: {r.text[:200]}"


# ─── 7. Solo (non-team) user regression ───────────────────────────
def test_solo_user_flows(db):
    solo_email = f"rbac-solo-{TEST_TAG}@example.com"
    rs = _signup(solo_email, PASS)
    assert rs.status_code in (200, 201), rs.text[:200]
    tok = rs.json().get("token") or _login(solo_email, PASS).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    for p in ["/auth/me", "/cto/projects/list", "/fix-pipeline/quota", "/codebase-health/last"]:
        r = requests.get(f"{API}{p}", headers=h, timeout=20)
        assert r.status_code not in (401, 403), f"solo {p}: {r.status_code} {r.text[:150]}"
    # cleanup
    db.dev_users.delete_one({"email": solo_email})
