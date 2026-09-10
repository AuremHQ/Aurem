"""
Backend tests for fal.ai BYOK key + Universal Key fallback contract.
Covers: GET / PUT / DELETE /api/aurem-dev/admin/video/secrets(/fal-key)
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"
FAKE_FAL = "fal_test_key_12345"


@pytest.fixture(scope="module")
def token():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/aurem-dev/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"Login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    if not tok:
        pytest.skip(f"No token in login response: {data}")
    return tok


@pytest.fixture
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_get_secrets_requires_auth():
    r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/video/secrets", timeout=10)
    assert r.status_code in (401, 403), f"got {r.status_code}"


def test_delete_fal_requires_auth():
    r = requests.delete(f"{BASE_URL}/api/aurem-dev/admin/video/secrets/fal-key", timeout=10)
    assert r.status_code in (401, 403), f"got {r.status_code}"


def test_put_fal_key_then_get_reflects_it(headers):
    # PUT
    r = requests.put(f"{BASE_URL}/api/aurem-dev/admin/video/secrets",
                     json={"fal_api_key": FAKE_FAL}, headers=headers, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("fal_configured") is True
    assert FAKE_FAL[-4:] in (data.get("fal_key_hint") or "")

    # GET reflects
    r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/video/secrets", headers=headers, timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d["fal_configured"] is True
    assert FAKE_FAL[-4:] in (d.get("fal_key_hint") or "")
    # Deepgram field should still be present
    assert "deepgram_configured" in d


def test_delete_fal_key_reverts_to_fallback(headers):
    r = requests.delete(f"{BASE_URL}/api/aurem-dev/admin/video/secrets/fal-key",
                        headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["fal_configured"] is False
    assert not d.get("fal_key_hint")

    # GET confirms
    r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/video/secrets", headers=headers, timeout=10)
    assert r.status_code == 200
    assert r.json()["fal_configured"] is False


def test_put_with_no_fields_rejected(headers):
    r = requests.put(f"{BASE_URL}/api/aurem-dev/admin/video/secrets",
                     json={}, headers=headers, timeout=10)
    assert r.status_code == 400
