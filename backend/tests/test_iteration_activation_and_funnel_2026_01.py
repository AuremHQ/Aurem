"""
Backend regression coverage for this iteration's changes:
- Admin funnel dashboard (/admin/funnel) 403/200 contract
- Admin presence summary (fields + admin gate)
- Onboarding scan-website (fallback path, garbage URL, SSRF blocks, auth-required)
- Onboarding demo-project run (verified LLM fix)
"""
import os
import random
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    return r.json().get("token")


@pytest.fixture(scope="module")
def user_token():
    email = f"iter-nonadmin-{random.randint(100000, 999999)}@example.com"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": "TestPass2026!", "name": "NonAdmin",
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    return r.json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


class TestAdminFunnel:
    def test_funnel_forbidden_for_non_admin(self, user_token):
        r = requests.get(f"{API}/admin/funnel", headers=_hdr(user_token), timeout=15)
        assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text[:200]}"

    def test_funnel_unauth_rejected(self):
        r = requests.get(f"{API}/admin/funnel", timeout=15)
        assert r.status_code in (401, 403)

    def test_funnel_ok_for_admin(self, admin_token):
        r = requests.get(f"{API}/admin/funnel", headers=_hdr(admin_token), timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        # payload contract: has funnel event counts + retention pcts + range fields
        assert isinstance(data, dict) and data, "empty funnel payload"
        assert "event_counts" in data or "counts" in data or "funnel" in data, f"missing counts: {list(data.keys())}"


class TestPresenceSummary:
    def test_presence_forbidden_for_non_admin(self, user_token):
        r = requests.get(f"{API}/admin/presence/summary", headers=_hdr(user_token), timeout=15)
        assert r.status_code == 403

    def test_presence_ok_admin(self, admin_token):
        r = requests.get(f"{API}/admin/presence/summary", headers=_hdr(admin_token), timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        for f in ("total_users", "active_24h", "live_now"):
            assert f in data, f"missing field {f} in {data}"
        assert isinstance(data["total_users"], int)
        assert isinstance(data["active_24h"], int)
        assert isinstance(data["live_now"], int)


class TestScanWebsite:
    def test_scan_fallback_path(self, user_token):
        r = requests.post(f"{API}/onboarding/scan-website",
                          json={"url": "example.com"}, headers=_hdr(user_token), timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        # Firecrawl exhausted in this env — expect fallback
        assert data.get("scan_path") == "fallback", f"expected fallback, got: {data.get('scan_path')}"
        assert isinstance(data["issues"], list)

    def test_scan_garbage_url_400(self, user_token):
        r = requests.post(f"{API}/onboarding/scan-website",
                          json={"url": "not a url at all"}, headers=_hdr(user_token), timeout=10)
        assert r.status_code == 400

    def test_scan_ssrf_internal_blocked(self, user_token):
        r = requests.post(f"{API}/onboarding/scan-website",
                          json={"url": "http://169.254.169.254/latest/meta-data/"},
                          headers=_hdr(user_token), timeout=10)
        assert r.status_code == 400

    def test_scan_requires_auth(self):
        r = requests.post(f"{API}/onboarding/scan-website",
                          json={"url": "example.com"}, timeout=10)
        assert r.status_code in (401, 403)


class TestDemoProject:
    def test_demo_run_verified(self, user_token):
        r = requests.post(f"{API}/onboarding/demo-project/run",
                          headers=_hdr(user_token), timeout=90)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        if not data.get("verified"):
            # one retry as noted in review request
            r2 = requests.post(f"{API}/onboarding/demo-project/run",
                               headers=_hdr(user_token), timeout=90)
            assert r2.status_code == 200
            data = r2.json()
        assert data.get("verified") is True, f"demo not verified: {data}"
        assert data.get("after_result") == 50.0
        assert "diff" in data and data["diff"]
