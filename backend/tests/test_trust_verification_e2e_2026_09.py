"""E2E backend tests for Trust & Verification Round (Enh 1-6)."""
import os
import time
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    # Try common login endpoints
    for path in ("/api/auth/login", "/api/aurem-dev/auth/login", "/api/login"):
        try:
            r = requests.post(f"{BASE}{path}", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                tok = data.get("token") or data.get("access_token") or (data.get("data") or {}).get("token")
                if tok:
                    print(f"Login via {path}")
                    return tok
        except Exception as e:
            print(f"login {path} err: {e}")
    pytest.skip("cannot login — no known auth endpoint returned token")


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --------- Enhancement 4: Trust calibration ---------
def test_trust_calibration_admin(headers):
    r = requests.get(f"{BASE}/api/aurem-dev/admin/trust-calibration", headers=headers, timeout=20)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("ok") is True
    snap = j.get("snapshot") or {}
    assert "denominator" in snap and "numerator" in snap
    assert "correction_rate_pct" in snap
    assert "not_enough_data" in snap
    assert snap.get("window_days") == 30
    if snap.get("denominator", 0) == 0:
        assert snap.get("not_enough_data") is True
        assert snap.get("correction_rate_pct") is None


def test_trust_calibration_requires_admin():
    # Without auth header, should be 401/403
    r = requests.get(f"{BASE}/api/aurem-dev/admin/trust-calibration", timeout=15)
    assert r.status_code in (401, 403), f"unauth got {r.status_code}"


# --------- Verification endpoints - basic shape ---------
def test_verification_nonexistent_task(headers):
    r = requests.get(f"{BASE}/api/aurem-dev/verification/tasks/nonexistent", headers=headers, timeout=15)
    assert r.status_code == 404, r.text


def test_report_issue_on_nonexistent(headers):
    r = requests.post(
        f"{BASE}/api/aurem-dev/verification/tasks/nonexistent-xyz/report-issue",
        headers=headers,
        json={"reason": "not working"},
        timeout=15,
    )
    # Either 404 or ok:false — must NOT downgrade
    if r.status_code == 200:
        j = r.json()
        assert j.get("downgraded") is not True
    else:
        assert r.status_code in (404, 400, 403)


def test_reverify_on_nonexistent(headers):
    r = requests.post(
        f"{BASE}/api/aurem-dev/verification/tasks/nonexistent-xyz/re-verify",
        headers=headers,
        timeout=15,
    )
    assert r.status_code in (404, 400, 200)
    if r.status_code == 200:
        j = r.json()
        # must not claim restored on a nonexistent task
        assert j.get("restored") is not True


# --------- Enhancement 5: scope confirmation cancel endpoint shape ---------
def test_scope_confirm_cancel_nonexistent(headers):
    r = requests.post(
        f"{BASE}/api/aurem-dev/verification/scope-confirm/nonexistent-proposal/cancel",
        headers=headers,
        timeout=15,
    )
    assert r.status_code in (200, 404, 400)
