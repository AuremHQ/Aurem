"""Live E2E for Issue 1 (GitHub status happy-path regression) and Issue 2
(free-tier edit-gating fix).

Runs against the preview backend at REACT_APP_BACKEND_URL.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    "https://bin-context-pat.preview.emergentagent.com"

FREE_EMAIL = "free-gate-test-0822@aurem.dev"
FREE_PASS = "FreeGateTest2026!"

FOUNDER_EMAIL = "test@aurem.dev"
FOUNDER_PASS = "AuremTest2026!"


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    # Try common auth endpoints
    for path in ("/api/aurem-dev/auth/login", "/api/auth/login"):
        r = s.post(f"{BASE_URL}{path}",
                   json={"email": email, "password": password}, timeout=20)
        if r.status_code == 200:
            data = r.json()
            token = data.get("token") or data.get("access_token")
            if token:
                s.headers["Authorization"] = f"Bearer {token}"
            return s
    pytest.skip(f"Could not login {email}: {r.status_code} {r.text[:200]}")


@pytest.fixture(scope="module")
def free_session():
    return _login(FREE_EMAIL, FREE_PASS)


@pytest.fixture(scope="module")
def founder_session():
    return _login(FOUNDER_EMAIL, FOUNDER_PASS)


# --- Issue 2: free-tier usage snapshot ---
def test_free_tier_usage_snapshot(free_session):
    r = free_session.get(f"{BASE_URL}/api/aurem-dev/usage/me", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    print(f"USAGE SNAPSHOT: {data}")
    assert "tasks_this_month" in data or "tasks_remaining" in data or "plan" in data


# --- Issue 2: free-tier edit request no longer paywall-blocked ---
@pytest.mark.parametrize("prompt", [
    "update my opening hours",
    "change our phone number",
])
def test_free_tier_edit_not_short_circuited_to_upgrade_offer(free_session, prompt):
    # Read remaining first
    u = free_session.get(f"{BASE_URL}/api/aurem-dev/usage/me", timeout=15).json()
    remaining = u.get("tasks_remaining", 0)
    print(f"tasks_remaining={remaining} plan={u.get('plan')}")

    r = free_session.post(
        f"{BASE_URL}/api/aurem-dev/chat/send",
        json={"prompt": prompt, "session_id": "test-edit-gate-2026-01-13"},
        timeout=90,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
    body = r.json()
    provider = body.get("provider") or (body.get("meta") or {}).get("provider")
    print(f"provider={provider} keys={list(body.keys())}")
    # The old bug returned provider=='edit-tier-upgrade-offer' for every edit.
    assert provider != "edit-tier-upgrade-offer", \
        f"Regression: free-tier edit hit the removed upgrade-offer short-circuit! body={body}"
    # If quota happened to be exhausted, provider will be fix-quota-exceeded — accept it too.
    if provider == "fix-quota-exceeded":
        meta = body.get("meta") or {}
        assert meta.get("quota_exceeded") is True
        print("Quota exhausted for this test account — quota-exceeded path is still correct.")


# --- Issue 2 regression: founder never sees upgrade offer ---
def test_founder_edit_no_upgrade_offer(founder_session):
    r = founder_session.post(
        f"{BASE_URL}/api/aurem-dev/chat/send",
        json={"prompt": "update my opening hours",
              "session_id": "test-founder-edit-2026-01-13"},
        timeout=90,
    )
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    provider = body.get("provider") or (body.get("meta") or {}).get("provider")
    print(f"founder provider={provider}")
    assert provider not in ("edit-tier-upgrade-offer", "fix-quota-exceeded"), body


# --- Issue 1 regression: GitHub connection-status endpoint returns 2xx ---
def test_github_connection_status_endpoint_reachable(founder_session):
    r = founder_session.get(
        f"{BASE_URL}/api/aurem-dev/cto/projects/connection-status",
        timeout=20,
    )
    print(f"connection-status HTTP {r.status_code} body={r.text[:300]}")
    assert r.status_code in (200, 204), f"unexpected {r.status_code}: {r.text[:200]}"
