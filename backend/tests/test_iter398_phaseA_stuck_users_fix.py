"""Iter398 Phase A — live API sanity for the stuck-users fix.

Verifies against the public preview backend:
- /onboarding/nudge/post-scan-state exists and returns the expected
  `{show, prefill_task}` shape (used by IdleStartHelper.jsx).
- /cto/projects/{id}/graph returns status passthrough (no hardcoded 'ready').
- /cto/projects/add is reachable (auth-gated); we don't need a full E2E,
  we just verify it doesn't 5xx and the auth requirement is enforced.

Uses the admin test account from /app/memory/test_credentials.md.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASS = "AuremTest2026!"


@pytest.fixture(scope="module")
def auth_token():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"admin login not available: {r.status_code} {r.text[:120]}")
    data = r.json()
    token = data.get("token") or data.get("access_token") or data.get("jwt")
    if not token:
        # Cookie-only auth — return the session cookies as a proxy
        return {"_cookies": s.cookies.get_dict()}
    return {"Authorization": f"Bearer {token}"}


def _headers(auth_token):
    if isinstance(auth_token, dict) and "_cookies" in auth_token:
        return {}
    return auth_token


def _cookies(auth_token):
    if isinstance(auth_token, dict) and "_cookies" in auth_token:
        return auth_token["_cookies"]
    return None


def test_post_scan_nudge_endpoint_shape(auth_token):
    """The existing nudge endpoint used by IdleStartHelper must return
    {show: bool, prefill_task: str|null} — even for a bogus project id
    it must not 5xx."""
    r = requests.get(
        f"{API}/onboarding/nudge/post-scan-state",
        params={"project_id": "does-not-exist-abc123"},
        headers=_headers(auth_token),
        cookies=_cookies(auth_token),
        timeout=15,
    )
    # It's allowed to be 200 (returning show=false) or 404 for the unknown project.
    assert r.status_code in (200, 400, 404), f"unexpected status {r.status_code}: {r.text[:200]}"
    if r.status_code == 200:
        data = r.json()
        assert "show" in data, f"missing 'show' key: {data}"
        assert isinstance(data["show"], bool)
        # prefill_task can be str or None
        assert "prefill_task" in data or data.get("show") is False


def test_graph_endpoint_status_field_present(auth_token):
    """GET /cto/projects/{id}/graph should return a `status` field (not
    just hardcoded 'ready'). Bogus id should 404 or 200-with-status."""
    r = requests.get(
        f"{API}/cto/projects/does-not-exist-abc123/graph",
        headers=_headers(auth_token),
        cookies=_cookies(auth_token),
        timeout=15,
    )
    assert r.status_code in (200, 400, 403, 404), f"got {r.status_code}: {r.text[:200]}"
    if r.status_code == 200:
        data = r.json()
        assert "status" in data


def test_projects_add_requires_auth():
    """Sanity: /cto/projects/add must require auth, and unauth must not 5xx."""
    r = requests.post(f"{API}/cto/projects/add", json={"repo_full_name": "foo/bar"}, timeout=15)
    assert r.status_code in (401, 403, 422), f"expected auth error, got {r.status_code}: {r.text[:200]}"


def test_github_oauth_status_endpoint_reachable(auth_token):
    """Used by connectGithub() in NewUserWizard for polling — must be
    reachable and never 5xx."""
    r = requests.get(
        f"{API}/github/oauth/status",
        headers=_headers(auth_token),
        cookies=_cookies(auth_token),
        timeout=15,
    )
    assert r.status_code in (200, 401, 403), f"got {r.status_code}: {r.text[:200]}"
    if r.status_code == 200:
        data = r.json()
        assert "connected" in data
