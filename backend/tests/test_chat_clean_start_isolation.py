# Chat history clean-start-on-login round — backend regression:
# sessions list ordering + per-user isolation of /chat/history & /chat/sessions.
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    if r.status_code != 200 or "token" not in r.json():
        pytest.skip(f"login failed {r.status_code} {r.text[:200]}")
    return r.json()["token"]


@pytest.fixture(scope="module")
def owner_hdr():
    return {"Authorization": f"Bearer {_login('test@aurem.dev', 'AuremTest2026!')}"}


@pytest.fixture(scope="module")
def other_hdr():
    email = f"TEST_iso_{int(time.time())}@example.com".lower()
    r = requests.post(f"{API}/auth/signup", json={"email": email, "password": "IsoTest2026!x", "name": "TEST Iso"}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"signup failed {r.status_code} {r.text[:200]}")
    tok = r.json().get("token") or _login(email, "IsoTest2026!x")
    return {"Authorization": f"Bearer {tok}"}


# Sessions list — most-recent-first
def test_owner_sessions_sorted_desc(owner_hdr):
    r = requests.get(f"{API}/chat/sessions", headers=owner_hdr, params={"project_id": "p_demo_a"}, timeout=30)
    assert r.status_code == 200
    s = r.json()["sessions"]
    assert len(s) > 0
    ts = [x.get("updated_at") for x in s if x.get("updated_at")]
    assert ts == sorted(ts, reverse=True)
    assert all("_id" not in x for x in s)


# Owner can read own session content
def test_owner_history_x_has_messages(owner_hdr):
    r = requests.get(f"{API}/chat/history", headers=owner_hdr, params={"session_id": "x"}, timeout=30)
    assert r.status_code == 200
    assert len(r.json()["messages"]) > 0


# Isolation — new user sees no sessions and cannot read owner's session
def test_other_user_sessions_empty(other_hdr):
    r = requests.get(f"{API}/chat/sessions", headers=other_hdr, timeout=30)
    assert r.status_code == 200
    assert r.json()["sessions"] == []


def test_other_user_cannot_read_owner_session(other_hdr):
    r = requests.get(f"{API}/chat/history", headers=other_hdr, params={"session_id": "x"}, timeout=30)
    assert r.status_code == 200
    assert r.json()["messages"] == []


def test_history_requires_auth():
    r = requests.get(f"{API}/chat/history", params={"session_id": "x"}, timeout=30)
    assert r.status_code in (401, 403)
