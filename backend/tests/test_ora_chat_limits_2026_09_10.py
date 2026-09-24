"""Backend tests for the editable Admin ORA Chat limits (2026-09-10).

Founder-reported bug: `daily_token_cap` was a hardcoded env var with
no admin UI to change it, and no relation to the $ balance shown in
the Admin ORA Chat header — hitting it while $ balance was fine
looked like a bug. Verifies the DB-override-wins-over-env-var
mechanism (services/ora_chat_v2/limits_store.py) and the new
GET/PUT /admin/llm/ora-chat-limits endpoints, restoring defaults
after each test so it doesn't leak state into other tests/environments.
"""
import os
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    try:
        for _ln in open("/app/frontend/.env"):
            if _ln.strip().startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = _ln.split("=", 1)[1].strip().rstrip("/")
                break
    except FileNotFoundError:
        pass
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"
DEFAULT_RATE = 20
DEFAULT_CAP = 200000


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    if data.get("mfa_required"):
        pytest.skip("MFA required — cannot proceed without TOTP")
    assert data.get("is_admin"), f"user is not admin: {data}"
    return data["token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def restore_defaults(headers):
    yield
    requests.put(f"{API}/admin/llm/ora-chat-limits", headers=headers,
                 json={"rate_limit_per_hour": DEFAULT_RATE, "daily_token_cap": DEFAULT_CAP}, timeout=15)


def test_ora_chat_limits_requires_auth():
    r = requests.get(f"{API}/admin/llm/ora-chat-limits", timeout=15)
    assert r.status_code in (401, 403), r.text
    r = requests.put(f"{API}/admin/llm/ora-chat-limits", json={"daily_token_cap": 1}, timeout=15)
    assert r.status_code in (401, 403), r.text


def test_ora_chat_limits_default_matches_env(headers):
    r = requests.get(f"{API}/admin/llm/ora-chat-limits", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rate_limit_per_hour"] == DEFAULT_RATE
    assert body["daily_token_cap"] == DEFAULT_CAP


def test_ora_chat_limits_update_and_readback(headers):
    r = requests.put(f"{API}/admin/llm/ora-chat-limits", headers=headers,
                      json={"daily_token_cap": 500000}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["daily_token_cap"] == 500000
    assert r.json()["rate_limit_per_hour"] == DEFAULT_RATE  # untouched field unchanged

    r2 = requests.get(f"{API}/admin/llm/ora-chat-limits", headers=headers, timeout=15)
    assert r2.json()["daily_token_cap"] == 500000


def test_ora_chat_limits_zero_means_unlimited_and_is_accepted(headers):
    r = requests.put(f"{API}/admin/llm/ora-chat-limits", headers=headers,
                      json={"daily_token_cap": 0, "rate_limit_per_hour": 0}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json() == {"rate_limit_per_hour": 0, "daily_token_cap": 0}


def test_ora_chat_limits_rejects_negative(headers):
    r = requests.put(f"{API}/admin/llm/ora-chat-limits", headers=headers,
                      json={"daily_token_cap": -5}, timeout=15)
    assert r.status_code == 422, r.text


def test_ora_chat_limits_requires_at_least_one_field(headers):
    r = requests.put(f"{API}/admin/llm/ora-chat-limits", headers=headers, json={}, timeout=15)
    assert r.status_code == 400, r.text
