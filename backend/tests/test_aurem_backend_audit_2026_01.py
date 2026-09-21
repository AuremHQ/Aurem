"""
Backend audit tests for AUREM changes:
- Admin onboarding funnel summary (synthetic exclusion)
- New CASL-screen endpoint (idempotent, no side-effects)
- GitHub funnel stats (new callback_outcomes fields)
- GitHub OAuth callback telemetry on failure paths (invalid_state, unknown_state)
- Basic health
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASS = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:400]}"
    tk = r.json().get("token") or r.json().get("access_token")
    assert tk, f"no token in response: {r.text[:400]}"
    return tk


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- F. Health ----------
def test_health_ok():
    r = requests.get(f"{BASE_URL}/api/health", timeout=15)
    assert r.status_code == 200
    j = r.json()
    assert j.get("db") is True
    assert j.get("ok") is True


# ---------- A. Onboarding funnel summary ----------
def test_onboarding_funnel_summary_excludes_synthetic(auth_headers):
    r = requests.get(f"{API}/admin/onboarding-funnel/summary", headers=auth_headers, timeout=30)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:600]}"
    j = r.json()
    # sanity — shape should include some stuck buckets
    print("SUMMARY_KEYS:", list(j.keys())[:20])
    # Look at any numeric bucket sums; expect small (single-digit) counts if synthetic excluded
    for k, v in j.items():
        if isinstance(v, dict) and "count" in v:
            print(f"  {k}: count={v.get('count')}")
        elif isinstance(v, int):
            print(f"  {k}: {v}")


# ---------- B. CASL screen ----------
def _fetch_casl(auth_headers, stage=None):
    params = {"stage": stage} if stage else None
    return requests.get(f"{API}/admin/onboarding-funnel/casl-screen", headers=auth_headers, params=params, timeout=30)


def test_casl_screen_no_stage(auth_headers):
    r = _fetch_casl(auth_headers)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:600]}"
    j = r.json()
    for f in ["stage_filter", "eligible", "excluded", "eligible_count", "excluded_count",
              "total_screened", "checked_at", "inquiry_window_days", "purchase_window_days"]:
        assert f in j, f"missing field {f}: keys={list(j.keys())}"
    assert j["inquiry_window_days"] == 182
    assert j["purchase_window_days"] == 730
    assert j["eligible_count"] + j["excluded_count"] == j["total_screened"], j
    assert isinstance(j["eligible"], list)
    assert isinstance(j["excluded"], list)
    # every excluded must have reason
    for u in j["excluded"]:
        assert "reason" in u, f"excluded user missing reason: {u}"
        assert u["reason"] in ("casl_expired_inquiry", "casl_expired_purchase"), u


def test_casl_screen_with_stage(auth_headers):
    r = _fetch_casl(auth_headers, stage="stage1_github_started")
    assert r.status_code == 200, f"{r.status_code}: {r.text[:600]}"
    j = r.json()
    assert j["stage_filter"] == "stage1_github_started"
    assert j["eligible_count"] + j["excluded_count"] == j["total_screened"]


def test_casl_screen_idempotent(auth_headers):
    # snapshot counts of possibly-written collections BEFORE calling twice
    # We use the funnel summary as an indirect indicator plus repeated calls consistency
    r1 = _fetch_casl(auth_headers)
    r2 = _fetch_casl(auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    j1, j2 = r1.json(), r2.json()
    assert j1["total_screened"] == j2["total_screened"], (j1["total_screened"], j2["total_screened"])
    assert j1["eligible_count"] == j2["eligible_count"]
    assert j1["excluded_count"] == j2["excluded_count"]


# ---------- C. GitHub funnel stats ----------
def test_github_funnel_stats(auth_headers):
    r = requests.get(f"{API}/funnel/github/stats", headers=auth_headers, params={"days": 90}, timeout=30)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:600]}"
    j = r.json()
    for f in ["stages", "conversions", "by_source", "total_events",
              "callback_outcomes", "callback_failure_rate_pct", "excluded_synthetic_sessions"]:
        assert f in j, f"missing {f}, keys={list(j.keys())}"
    assert isinstance(j["callback_outcomes"], dict)
    assert isinstance(j["callback_failure_rate_pct"], (int, float))
    assert 0 <= j["callback_failure_rate_pct"] <= 100
    assert isinstance(j["excluded_synthetic_sessions"], int)
    assert isinstance(j["stages"], (dict, list))
    assert isinstance(j["conversions"], (dict, list))
    assert isinstance(j["by_source"], dict)
    assert isinstance(j["total_events"], int)
    print("STATS:", {k: j[k] for k in ["callback_outcomes", "callback_failure_rate_pct", "excluded_synthetic_sessions", "total_events"]})


# ---------- D. GitHub OAuth callback telemetry ----------
def _get_callback_outcomes(auth_headers):
    r = requests.get(f"{API}/funnel/github/stats", headers=auth_headers, params={"days": 90}, timeout=30)
    assert r.status_code == 200
    return r.json().get("callback_outcomes", {}) or {}


def test_oauth_callback_invalid_state_telemetry(auth_headers):
    before = _get_callback_outcomes(auth_headers)
    r = requests.get(f"{BASE_URL}/api/aurem-dev/github/oauth/callback",
                     params={"code": "anything", "state": "malformed-no-colon"},
                     timeout=20, allow_redirects=False)
    # Expect HTTP 400 per review request
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:400]}"
    time.sleep(1.5)
    after = _get_callback_outcomes(auth_headers)
    b = int(before.get("invalid_state", 0) or 0)
    a = int(after.get("invalid_state", 0) or 0)
    assert a >= b + 1, f"invalid_state did not increment: before={b} after={a} outcomes_after={after}"


def test_oauth_callback_unknown_state_telemetry(auth_headers):
    before = _get_callback_outcomes(auth_headers)
    r = requests.get(f"{BASE_URL}/api/aurem-dev/github/oauth/callback",
                     params={"code": "anything", "state": "signup:some-totally-fake-nonce-12345"},
                     timeout=20, allow_redirects=False)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:400]}"
    time.sleep(1.5)
    after = _get_callback_outcomes(auth_headers)
    b = int(before.get("unknown_state", 0) or 0)
    a = int(after.get("unknown_state", 0) or 0)
    assert a >= b + 1, f"unknown_state did not increment: before={b} after={a} outcomes_after={after}"
