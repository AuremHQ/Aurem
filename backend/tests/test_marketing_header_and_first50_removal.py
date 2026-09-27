"""
Backend tests for the marketing-header consolidation + first-50 promo removal
(iteration after 396). Verifies:
  - /api/aurem-dev/promo/first50/status is fully removed (404)
  - /api/health is ok:true, has build_hash, no dead supervised tasks
  - /api/aurem-dev/auth/resend-verification exists
  - /api/aurem-dev/auth/verify with invalid token returns proper redirect
  - unsubscribe endpoint still routes (email_link_tokens.py refactor)
"""
import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fall back to frontend/.env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass

API = f"{BASE_URL}/api/aurem-dev"


def test_health_ok_no_dead_tasks():
    r = requests.get(f"{BASE_URL}/api/health", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ok") is True, data
    assert "build_hash" in data, data
    supervised = data.get("supervised_tasks") or {}
    dead = supervised.get("dead", [])
    assert dead == [], f"Found dead supervised tasks: {dead}"


def test_promo_first50_status_removed():
    r = requests.get(f"{API}/promo/first50/status", timeout=10)
    assert r.status_code == 404, f"Expected 404 after promo removal, got {r.status_code}: {r.text[:200]}"


def test_promo_first50_waitlist_removed():
    r = requests.post(f"{API}/promo/first50/waitlist", json={"email": "x@x.com"}, timeout=10)
    assert r.status_code == 404, f"Expected 404, got {r.status_code}"


def test_verify_invalid_token_redirects_fail():
    r = requests.get(f"{API}/auth/verify", params={"token": "obviously-invalid-token-abc"}, allow_redirects=False, timeout=10)
    # Should redirect to /verify page with a failure status (not claimed=1)
    assert r.status_code in (302, 303, 307), f"Expected redirect, got {r.status_code}: {r.text[:200]}"
    loc = r.headers.get("location", "")
    assert "/verify" in loc, f"Expected redirect to /verify, got {loc}"
    assert "claimed=" not in loc, f"Old promo 'claimed' param leaked: {loc}"


def test_resend_verification_endpoint_exists():
    # No auth / no body -> should NOT be 404 (endpoint should exist)
    r = requests.post(f"{API}/auth/resend-verification", json={}, timeout=10)
    assert r.status_code != 404, f"resend-verification endpoint missing: {r.status_code}"


def test_unsubscribe_endpoint_exists():
    # Missing params -> should 400/422, not 404 (endpoint exists via email_link_tokens refactor)
    r = requests.get(f"{API}/unsubscribe", timeout=10)
    assert r.status_code != 404, f"unsubscribe endpoint missing (email_link_tokens refactor broken): {r.status_code}"


def test_first50_admin_endpoints_return_404():
    # The admin campaign router was deleted; any of its paths should be 404
    for path in ("/admin/first50-campaign/status", "/admin/first50/campaign", "/admin/first50-campaign"):
        r = requests.get(f"{API}{path}", timeout=10)
        assert r.status_code == 404, f"Expected 404 for {path}, got {r.status_code}"
