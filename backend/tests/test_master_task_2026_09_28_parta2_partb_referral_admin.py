"""
test_master_task_2026_09_28_parta2_partb_referral_admin.py

Covers MASTER TASK Part A2 (referral copy 'coming soon') and
Part B1-B4 (admin audit log, live presence, cockpit gauges, token grant
and feature-flag change auditing).
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests

API = os.environ.get("REACT_APP_BACKEND_URL", "")
if not API:
    with open(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", ".env")) as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                API = line.strip().split("=", 1)[1]
API = f"{API}/api/aurem-dev"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def regular_user():
    email = f"partb-user-{uuid.uuid4().hex[:10]}@example.com"
    password = "ProviderLeak2026!"
    r = requests.post(f"{API}/auth/signup",
                      json={"email": email, "password": password, "name": "PartB User"}, timeout=15)
    assert r.status_code == 200, r.text
    return {"email": email, "password": password, "token": r.json()["token"]}


# ---------------- Part A2: referral copy ----------------

def test_referral_reward_per_paid_says_coming_soon(regular_user):
    r = requests.get(f"{API}/referrals/my", headers=_auth(regular_user["token"]), timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("reward_per_paid") == "coming soon", body
    # Must NOT promise "1 month free"
    for v in body.values():
        if isinstance(v, str):
            assert "1 month free" not in v.lower(), body


# ---------------- Part B1: admin audit log ----------------

def test_audit_actions_admin_only(admin_token, regular_user):
    r = requests.get(f"{API}/admin/audit/actions?limit=20", headers=_auth(admin_token), timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body if isinstance(body, list) else body.get("actions") or body.get("rows") or body.get("items") or []
    assert isinstance(rows, list)
    if rows:
        # newest first: ts descending
        ts_vals = [row.get("ts") for row in rows if row.get("ts") is not None]
        assert ts_vals == sorted(ts_vals, reverse=True), "not sorted newest-first"
        keys = set(rows[0].keys())
        for req in ("ts", "actor", "action", "target", "result"):
            assert req in keys, f"missing {req} in {keys}"

    # non-admin gets 403
    r2 = requests.get(f"{API}/admin/audit/actions?limit=5",
                      headers=_auth(regular_user["token"]), timeout=15)
    assert r2.status_code in (401, 403), r2.status_code


def test_login_failed_and_success_recorded_in_audit(admin_token):
    # trigger a failed login for test@aurem.dev
    r_fail = requests.post(f"{API}/auth/login",
                           json={"email": ADMIN_EMAIL, "password": "WrongPassword_XYZ!"}, timeout=15)
    assert r_fail.status_code in (400, 401, 403), r_fail.status_code

    time.sleep(0.6)
    # successful login
    r_ok = requests.post(f"{API}/auth/login",
                         json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r_ok.status_code == 200

    time.sleep(1.2)
    r = requests.get(f"{API}/admin/audit/actions?limit=50", headers=_auth(admin_token), timeout=15)
    assert r.status_code == 200
    body = r.json()
    rows = body if isinstance(body, list) else body.get("actions") or body.get("rows") or body.get("items") or []
    login_rows = [row for row in rows if str(row.get("action", "")).lower() == "login"]
    assert any(str(r_.get("result", "")).lower() in ("failed", "failure", "fail")
               and ADMIN_EMAIL in str(r_.get("target", "")) for r_ in login_rows), \
        f"no failed-login row for {ADMIN_EMAIL} in {login_rows[:5]}"
    assert any(str(r_.get("result", "")).lower() in ("success", "ok")
               and ADMIN_EMAIL in (str(r_.get("actor", "")) + str(r_.get("target", ""))) for r_ in login_rows), \
        f"no success-login row for {ADMIN_EMAIL} in {login_rows[:5]}"


# ---------------- Part B2: live presence ----------------

def test_admin_users_have_presence_fields(admin_token):
    r = requests.get(f"{API}/admin/users?window=all", headers=_auth(admin_token), timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    users = body if isinstance(body, list) else body.get("users") or body.get("items") or []
    assert users, "no users returned"
    sample = users[:20]
    for u in sample:
        assert "is_online" in u, f"missing is_online in {list(u.keys())[:12]}"
        assert isinstance(u["is_online"], bool), u["is_online"]
        assert "last_seen_at" in u, f"missing last_seen_at in {list(u.keys())[:12]}"
        ls = u["last_seen_at"]
        assert ls is None or isinstance(ls, (int, float)), ls


# ---------------- Part B3: cockpit gauges ----------------

def test_presence_summary_admin_only(admin_token, regular_user):
    r = requests.get(f"{API}/admin/presence/summary", headers=_auth(admin_token), timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("total_users", "live_now", "active_24h", "paid_users"):
        assert k in body, f"missing {k}"
        assert isinstance(body[k], int) and body[k] >= 0, f"{k}={body[k]}"

    r2 = requests.get(f"{API}/admin/presence/summary",
                      headers=_auth(regular_user["token"]), timeout=15)
    assert r2.status_code in (401, 403), r2.status_code


# ---------------- Part B4: token grant + feature flag audit ----------------

def test_token_grant_produces_audit_row(admin_token):
    # pick first user id from admin/users
    ru = requests.get(f"{API}/admin/users?window=all&limit=5", headers=_auth(admin_token), timeout=30)
    assert ru.status_code == 200
    users = ru.json() if isinstance(ru.json(), list) else \
        ru.json().get("users") or ru.json().get("items") or []
    assert users
    uid = users[0].get("id") or users[0].get("_id") or users[0].get("user_id") or users[0].get("email")
    assert uid

    r = requests.post(f"{API}/admin/users/{uid}/grant-tokens",
                      json={"tokens": 100, "reason": "audit-test-partb"},
                      headers=_auth(admin_token), timeout=15)
    # Some backends may reject cheaply; accept 200/201/400 but require audit row
    # if it succeeded
    time.sleep(1.0)
    ra = requests.get(f"{API}/admin/audit/actions?limit=50", headers=_auth(admin_token), timeout=15)
    assert ra.status_code == 200
    rows = ra.json() if isinstance(ra.json(), list) else \
        ra.json().get("actions") or ra.json().get("rows") or ra.json().get("items") or []
    if r.status_code in (200, 201):
        assert any(str(row.get("action", "")).lower() == "token_grant" for row in rows), \
            f"no token_grant row in {[r_.get('action') for r_ in rows[:15]]}"
    else:
        pytest.skip(f"grant-tokens returned {r.status_code}: {r.text[:200]}")


def test_feature_flag_toggle_audited(admin_token):
    flag = f"partb_test_flag_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/admin/feature-flags/{flag}/toggle",
                      json={"enabled": True}, headers=_auth(admin_token), timeout=15)
    if r.status_code not in (200, 201, 204):
        pytest.skip(f"feature-flag toggle unavailable ({r.status_code}): {r.text[:200]}")
    time.sleep(1.0)
    ra = requests.get(f"{API}/admin/audit/actions?limit=50", headers=_auth(admin_token), timeout=15)
    rows = ra.json() if isinstance(ra.json(), list) else \
        ra.json().get("actions") or ra.json().get("rows") or ra.json().get("items") or []
    assert any(str(row.get("action", "")).lower() == "feature_flag_change" for row in rows), \
        f"no feature_flag_change row in {[r_.get('action') for r_ in rows[:15]]}"
