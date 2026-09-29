"""Live E2E over REACT_APP_BACKEND_URL (public preview) — verifies the bare-root
.well-known discovery bug fix (this is the actual production regression being
patched). Hits the public URL through the K8s ingress, so it mirrors real client
behavior (Claude).
"""
import base64
import hashlib
import os
import secrets
import time
import urllib.parse
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL not set"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"
MCP_PATH = "/api/aurem-dev/mcp/ora-chat"
ISSUER = f"{BASE_URL}/api/aurem-dev"


def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


@pytest.fixture(scope="module")
def session():
    return requests.Session()


# ---------------------------------------------------------------------------
# 1. Discovery under /api-prefix (backend direct)
# ---------------------------------------------------------------------------
class TestDiscoveryPrefixed:
    def test_as_metadata_prefixed(self, session):
        r = session.get(f"{BASE_URL}/api/aurem-dev/.well-known/oauth-authorization-server")
        assert r.status_code == 200, r.text[:300]
        assert "application/json" in r.headers.get("content-type", "")
        j = r.json()
        assert j["issuer"].rstrip("/").endswith("/api/aurem-dev")
        assert j["registration_endpoint"].endswith("/oauth/register")
        assert "authorization_endpoint" in j and "token_endpoint" in j
        assert j["code_challenge_methods_supported"] == ["S256"]

    def test_prm_prefixed(self, session):
        r = session.get(f"{BASE_URL}/api/aurem-dev/.well-known/oauth-protected-resource")
        assert r.status_code == 200
        j = r.json()
        assert j["resource"].endswith("/api/aurem-dev/mcp/ora-chat")
        assert isinstance(j["authorization_servers"], list) and len(j["authorization_servers"]) >= 1
        assert j["authorization_servers"][0].rstrip("/").endswith("/api/aurem-dev")


# ---------------------------------------------------------------------------
# 2. THE BUG: bare-root discovery must be JSON, not the SPA HTML.
# ---------------------------------------------------------------------------
class TestBareRootDiscovery:
    @pytest.mark.parametrize(
        "path",
        [
            "/.well-known/oauth-authorization-server",
            "/.well-known/oauth-protected-resource",
            "/.well-known/openid-configuration",
        ],
    )
    def test_bare_root_returns_json_not_html(self, session, path):
        r = session.get(f"{BASE_URL}{path}", headers={"Accept": "application/json"})
        assert r.status_code == 200, f"{path} -> {r.status_code} body={r.text[:200]}"
        ctype = r.headers.get("content-type", "")
        assert "application/json" in ctype, f"{path} content-type is {ctype!r} not JSON. body={r.text[:200]}"
        body = r.text.lstrip()
        assert not body.startswith("<"), f"{path} returned HTML: {body[:200]}"
        j = r.json()
        assert isinstance(j, dict)
        # If it's the AS metadata, verify issuer matches the prefixed AS
        if path.endswith("oauth-authorization-server") or path.endswith("openid-configuration"):
            assert j.get("issuer", "").rstrip("/").endswith("/api/aurem-dev"), j.get("issuer")
        if path.endswith("oauth-protected-resource"):
            assert j.get("resource", "").endswith("/api/aurem-dev/mcp/ora-chat")


# ---------------------------------------------------------------------------
# 3. Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------
class TestDCR:
    def test_register_returns_client_id(self, session):
        r = session.post(
            f"{BASE_URL}/api/aurem-dev/oauth/register",
            json={"client_name": "TEST_live_dcr", "redirect_uris": ["https://example.com/cb"]},
        )
        assert r.status_code == 201, r.text[:400]
        j = r.json()
        assert j.get("client_id")
        assert "https://example.com/cb" in j.get("redirect_uris", [])

    def test_rejects_bad_redirect(self, session):
        r = session.post(
            f"{BASE_URL}/api/aurem-dev/oauth/register",
            json={"client_name": "TEST_bad", "redirect_uris": ["http://evil.example/cb"]},
        )
        assert r.status_code == 400

    def test_rejects_missing_redirects(self, session):
        r = session.post(
            f"{BASE_URL}/api/aurem-dev/oauth/register", json={"client_name": "TEST_missing"}
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 4. End-to-end admin-only flow for ORA Chat resource
# ---------------------------------------------------------------------------
class TestAdminOnlyFlow:
    @pytest.fixture(scope="class")
    def dcr_client(self, session):
        redirect_uri = "http://localhost:9999/callback"
        r = session.post(
            f"{BASE_URL}/api/aurem-dev/oauth/register",
            json={"client_name": "TEST_flow", "redirect_uris": [redirect_uri]},
        )
        assert r.status_code == 201
        return {"client_id": r.json()["client_id"], "redirect_uri": redirect_uri}

    def _authorize_params(self, dcr_client, resource):
        verifier, challenge = _pkce()
        state = uuid.uuid4().hex
        return verifier, {
            "response_type": "code",
            "client_id": dcr_client["client_id"],
            "redirect_uri": dcr_client["redirect_uri"],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
            "state": state,
        }

    def test_get_authorize_renders_login_form(self, session, dcr_client):
        _, params = self._authorize_params(
            dcr_client, f"{BASE_URL}/api/aurem-dev/mcp/ora-chat"
        )
        r = session.get(f"{BASE_URL}/api/aurem-dev/oauth/authorize", params=params, allow_redirects=False)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        assert "ADMIN-ONLY" in body or "admin-only" in body.lower()
        assert "oauth-login-form" in body

    def test_nonadmin_rejected_for_ora_chat(self, session, dcr_client):
        # create throwaway account
        email = f"TEST_nonadmin_{uuid.uuid4().hex[:8]}@example.com"
        pw = "TestPass2026!"
        signup = session.post(
            f"{BASE_URL}/api/aurem-dev/auth/signup", json={"email": email, "password": pw}
        )
        assert signup.status_code in (200, 201), signup.text[:200]

        _, params = self._authorize_params(
            dcr_client, f"{BASE_URL}/api/aurem-dev/mcp/ora-chat"
        )
        r = session.post(
            f"{BASE_URL}/api/aurem-dev/oauth/authorize",
            params=params,
            data={"email": email, "password": pw},
            allow_redirects=False,
        )
        assert r.status_code in (302, 303), r.text[:200]
        loc = r.headers["location"]
        assert "error=admin_required" in loc
        assert "code=" not in loc

    def test_nonadmin_allowed_for_other_resource(self, session, dcr_client):
        email = f"TEST_nonadmin_other_{uuid.uuid4().hex[:8]}@example.com"
        pw = "TestPass2026!"
        signup = session.post(
            f"{BASE_URL}/api/aurem-dev/auth/signup", json={"email": email, "password": pw}
        )
        assert signup.status_code in (200, 201)

        _, params = self._authorize_params(
            dcr_client, f"{BASE_URL}/api/aurem-dev/mcp/some-other"
        )
        r = session.post(
            f"{BASE_URL}/api/aurem-dev/oauth/authorize",
            params=params,
            data={"email": email, "password": pw},
            allow_redirects=False,
        )
        assert r.status_code in (302, 303), r.text[:300]
        loc = r.headers["location"]
        # Non-ora-chat resource must NOT be admin-gated
        assert "error=admin_required" not in loc, f"admin gating leaked to other resource: {loc}"
        assert "code=" in loc, f"expected code in redirect, got: {loc}"

    def test_admin_full_flow_token_exchange_and_mcp_call(self, session, dcr_client):
        verifier, params = self._authorize_params(
            dcr_client, f"{BASE_URL}/api/aurem-dev/mcp/ora-chat"
        )
        r = session.post(
            f"{BASE_URL}/api/aurem-dev/oauth/authorize",
            params=params,
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            allow_redirects=False,
        )
        assert r.status_code in (302, 303), r.text[:400]
        loc = r.headers["location"]
        assert "code=" in loc and f"state={params['state']}" in loc, loc
        code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]

        # Exchange
        tok = session.post(
            f"{BASE_URL}/api/aurem-dev/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": dcr_client["redirect_uri"],
                "client_id": dcr_client["client_id"],
                "code_verifier": verifier,
            },
        )
        assert tok.status_code == 200, tok.text[:400]
        tj = tok.json()
        access = tj["access_token"]
        assert access.startswith("sk-aurem-oauth-")
        assert tj.get("token_type", "").lower() == "bearer"

        # Reuse should fail
        tok2 = session.post(
            f"{BASE_URL}/api/aurem-dev/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": dcr_client["redirect_uri"],
                "client_id": dcr_client["client_id"],
                "code_verifier": verifier,
            },
        )
        assert tok2.status_code == 400

        # Call the MCP with the token
        mcp = session.post(
            f"{BASE_URL}{MCP_PATH}",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert mcp.status_code == 200, mcp.text[:300]
        mj = mcp.json()
        tools = mj.get("result", {}).get("tools", [])
        names = {t["name"] for t in tools}
        assert names == {"ask_ora_chat", "list_ora_conversations", "get_ora_conversation"}, names

        # Store token for revoke test via admin panel later
        pytest.oauth_access_token = access
        pytest.oauth_client_id = dcr_client["client_id"]


# ---------------------------------------------------------------------------
# 5. Unauthenticated MCP -> 401 with WWW-Authenticate pointing at bare PRM
# ---------------------------------------------------------------------------
class TestUnauthenticated:
    def test_401_www_authenticate_points_to_bare_prm(self, session):
        r = session.post(
            f"{BASE_URL}{MCP_PATH}",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert r.status_code == 401
        wa = r.headers.get("www-authenticate", "")
        assert 'resource_metadata=' in wa, wa
        assert "/.well-known/oauth-protected-resource" in wa, wa


# ---------------------------------------------------------------------------
# 6. Admin panel OAuth clients endpoints
# ---------------------------------------------------------------------------
class TestAdminPanel:
    @pytest.fixture(scope="class")
    def admin_jwt(self, session):
        r = session.post(
            f"{BASE_URL}/api/aurem-dev/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        assert r.status_code == 200, r.text[:300]
        return r.json()["token"]

    def test_list_oauth_clients(self, session, admin_jwt):
        r = session.get(
            f"{BASE_URL}/api/aurem-dev/admin/gateway/oauth-clients",
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        assert j.get("ok") is True
        assert isinstance(j.get("clients"), list)

    def test_revoke_oauth_client_kills_token(self, session, admin_jwt):
        cid = getattr(pytest, "oauth_client_id", None)
        access = getattr(pytest, "oauth_access_token", None)
        if not cid or not access:
            pytest.skip("Prior flow not completed")

        rv = session.post(
            f"{BASE_URL}/api/aurem-dev/admin/gateway/oauth-clients/{cid}/revoke",
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        assert rv.status_code == 200, rv.text[:300]

        # subsequent MCP call with old token must now be rejected
        mcp = session.post(
            f"{BASE_URL}{MCP_PATH}",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert mcp.status_code in (401, 403), mcp.status_code


# ---------------------------------------------------------------------------
# 7. Bearer-key regression (unrelated legacy path must still work)
# ---------------------------------------------------------------------------
class TestBearerKeyRegression:
    def test_admin_bearer_key_still_works_and_revoke(self, session):
        login = session.post(
            f"{BASE_URL}/api/aurem-dev/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        assert login.status_code == 200
        jwt = login.json()["token"]
        headers = {"Authorization": f"Bearer {jwt}"}

        create = session.post(
            f"{BASE_URL}/api/aurem-dev/admin/gateway/keys",
            headers=headers,
            json={"scope": "mcp", "label": "TEST_regression"},
        )
        assert create.status_code in (200, 201), create.text[:400]
        cj = create.json()
        raw_key = cj.get("key") or cj.get("raw_key") or cj.get("api_key")
        key_doc = cj.get("key_doc") or {}
        key_id = cj.get("key_id") or cj.get("id") or key_doc.get("key_id") or key_doc.get("id")
        assert raw_key and key_id, cj

        mcp = session.post(
            f"{BASE_URL}{MCP_PATH}",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert mcp.status_code == 200, mcp.text[:300]
        tools = mcp.json().get("result", {}).get("tools", [])
        assert len(tools) == 3

        # Revoke
        rv = session.post(
            f"{BASE_URL}/api/aurem-dev/admin/gateway/keys/{key_id}/revoke",
            headers=headers,
            params={"scope": "mcp"},
        )
        assert rv.status_code == 200, rv.text[:300]

        # After revoke -> 403 (or 401)
        mcp2 = session.post(
            f"{BASE_URL}{MCP_PATH}",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert mcp2.status_code in (401, 403), mcp2.status_code
