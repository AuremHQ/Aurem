"""Backend tests for admin -> user in-app notification feature.

Covers POST /api/aurem-dev/admin/users/notify:
- validation (empty message, no user_ids and no all)
- targeted send (1 user) and verification via GET /notifications
- all=true broadcast
"""
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def admin_user_id(admin_session):
    r = admin_session.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    uid = j.get("user_id") or j.get("id") or (j.get("user") or {}).get("user_id")
    assert uid, f"no user_id in /auth/me: {j}"
    return uid


def test_notify_missing_message(admin_session, admin_user_id):
    r = admin_session.post(f"{API}/admin/users/notify",
                           json={"user_ids": [admin_user_id], "message": "   "}, timeout=15)
    assert r.status_code == 400
    assert "message" in r.text.lower()


def test_notify_missing_targets(admin_session):
    r = admin_session.post(f"{API}/admin/users/notify",
                           json={"message": "hi"}, timeout=15)
    assert r.status_code == 400


def test_notify_targeted_and_verify_persistence(admin_session, admin_user_id):
    marker = f"TEST_admin_notify_{uuid.uuid4().hex[:8]}"
    r = admin_session.post(f"{API}/admin/users/notify",
                           json={"user_ids": [admin_user_id], "message": marker}, timeout=20)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body.get("ok") is True
    assert body.get("sent") == 1
    assert body.get("all") is False

    time.sleep(0.5)
    r2 = admin_session.get(f"{API}/notifications", timeout=15)
    assert r2.status_code == 200, r2.text[:300]
    j = r2.json()
    items = j.get("items") or j.get("notifications") or []
    found = [it for it in items if it.get("type") == "admin_message" and marker in (it.get("text") or "")]
    assert found, f"admin_message with marker not found in {len(items)} notifications; sample={items[:2]}"


def test_notify_all_broadcast(admin_session):
    marker = f"TEST_broadcast_{uuid.uuid4().hex[:8]}"
    r = admin_session.post(f"{API}/admin/users/notify",
                           json={"all": True, "message": marker}, timeout=120)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body.get("ok") is True
    assert body.get("all") is True
    assert body.get("sent", 0) >= 1
    # Recipients capped at 50
    assert len(body.get("recipients") or []) <= 50
