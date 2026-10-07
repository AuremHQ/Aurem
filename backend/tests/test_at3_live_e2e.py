"""Live E2E tests for AT3 (admin team sidebar access & request flow).

Hits the public preview backend URL. Uses existing fixtures:
- Owner: test@aurem.dev / AuremTest2026!
- Team member: sidebar-at3-test@example.com / SidebarAt3Test2026!
"""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

OWNER_EMAIL = "test@aurem.dev"
OWNER_PASS = "AuremTest2026!"
MEMBER_EMAIL = "sidebar-at3-test@example.com"
MEMBER_PASS = "SidebarAt3Test2026!"


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text[:300]}"
    data = r.json()
    tok = data.get("access_token") or data.get("token")
    assert tok, f"no token in login response: {data}"
    return tok


@pytest.fixture(scope="module")
def owner_token():
    return _login(OWNER_EMAIL, OWNER_PASS)


@pytest.fixture(scope="module")
def member_token():
    try:
        return _login(MEMBER_EMAIL, MEMBER_PASS)
    except AssertionError as e:
        pytest.skip(f"team member fixture unavailable: {e}")


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# --- /admin/team/my-access self-service ---
def test_member_my_access_returns_screens(member_token):
    r = requests.get(f"{API}/admin/team/my-access", headers=_auth(member_token), timeout=20)
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert "screens" in body
    assert "is_owner" in body
    assert body["is_owner"] is False
    # fixture should have at least one non-off screen
    non_off = {k: v for k, v in body["screens"].items() if v and v != "off"}
    assert non_off, f"expected non-off overrides, got {body['screens']}"


def test_owner_my_access_is_owner(owner_token):
    r = requests.get(f"{API}/admin/team/my-access", headers=_auth(owner_token), timeout=20)
    assert r.status_code == 200
    assert r.json().get("is_owner") is True


# --- access-requests flow ---
def test_member_can_create_access_request_and_owner_sees_it(member_token, owner_token):
    # choose an unlikely-already-held screen
    screen_key = "cockpit.home.home"
    payload = {"screen_key": screen_key, "note": "AT3 live e2e test"}
    r = requests.post(f"{API}/admin/team/access-requests", json=payload, headers=_auth(member_token), timeout=20)
    assert r.status_code in (200, 201), f"create req: {r.status_code} {r.text[:300]}"
    created = r.json()
    req_id = created.get("id") or created.get("request_id") or created.get("_id")
    assert req_id, f"no id in create resp: {created}"

    # Owner lists
    r2 = requests.get(f"{API}/admin/team/access-requests", headers=_auth(owner_token), timeout=20)
    assert r2.status_code == 200, r2.text[:300]
    reqs = r2.json()
    items = reqs if isinstance(reqs, list) else reqs.get("requests") or reqs.get("items") or []
    ids = [str(x.get("id") or x.get("_id") or x.get("request_id")) for x in items]
    assert str(req_id) in ids, f"created req {req_id} not in owner list ids={ids}"

    # Owner resolves (deny to avoid state pollution)
    r3 = requests.post(
        f"{API}/admin/team/access-requests/{req_id}/resolve",
        json={"action": "deny"},
        headers=_auth(owner_token),
        timeout=20,
    )
    assert r3.status_code in (200, 204), f"resolve: {r3.status_code} {r3.text[:300]}"


def test_member_cannot_list_access_requests(member_token):
    r = requests.get(f"{API}/admin/team/access-requests", headers=_auth(member_token), timeout=20)
    assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text[:200]}"


def test_member_cannot_resolve_access_request(member_token):
    r = requests.post(
        f"{API}/admin/team/access-requests/nonexistent-id/resolve",
        json={"action": "approve"},
        headers=_auth(member_token),
        timeout=20,
    )
    assert r.status_code == 403, f"expected 403, got {r.status_code}"


def test_resolve_does_not_grant_screen(member_token, owner_token):
    # snapshot current
    before = requests.get(f"{API}/admin/team/my-access", headers=_auth(member_token), timeout=20).json()["screens"]
    screen_key = "ora.autonomy.allowlist"
    assert before.get(screen_key, "off") in (None, "off", ""), f"screen already granted, test invalid: {before.get(screen_key)}"

    cr = requests.post(f"{API}/admin/team/access-requests", json={"screen_key": screen_key, "note": "no-grant check"}, headers=_auth(member_token), timeout=20)
    assert cr.status_code in (200, 201)
    rid = cr.json().get("id") or cr.json().get("_id") or cr.json().get("request_id")

    requests.post(f"{API}/admin/team/access-requests/{rid}/resolve", json={"action": "approve"}, headers=_auth(owner_token), timeout=20)

    after = requests.get(f"{API}/admin/team/my-access", headers=_auth(member_token), timeout=20).json()["screens"]
    assert after.get(screen_key, "off") in (None, "off", ""), f"approve auto-granted screen! after={after.get(screen_key)}"


# --- Regression: non-team-member non-founder cannot hit /admin endpoints ---
def test_unrelated_user_cannot_hit_my_access():
    import uuid
    email = f"at3-regression-{uuid.uuid4().hex[:8]}@example.com"
    pw = "RegressionTest2026!"
    sr = requests.post(f"{API}/auth/signup", json={"email": email, "password": pw, "name": "Reg Test"}, timeout=20)
    if sr.status_code not in (200, 201):
        pytest.skip(f"signup not available: {sr.status_code}")
    tok = sr.json().get("access_token") or sr.json().get("token")
    if not tok:
        tok = _login(email, pw)
    r = requests.get(f"{API}/admin/team/my-access", headers=_auth(tok), timeout=20)
    # my-access is self-service; a non-team-member should get is_owner False + empty screens, NOT 500
    assert r.status_code in (200, 403), f"unexpected {r.status_code} {r.text[:300]}"
    if r.status_code == 200:
        body = r.json()
        assert body.get("is_owner") is False
        screens = body.get("screens") or {}
        non_off = {k: v for k, v in screens.items() if v and v != "off"}
        assert not non_off, f"unrelated user got non-off screens: {non_off}"
