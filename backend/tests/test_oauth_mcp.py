"""
tests/test_oauth_mcp.py — 2026-09-29 OAuth 2.1 for the ORA Chat MCP server.

Regression coverage for:
  - POST /oauth/register              (Dynamic Client Registration, RFC 7591)
  - GET  /oauth/authorize              (PKCE S256, resource validation)
  - GET  /oauth/pending/{consent_id}   (admin-only)
  - POST /oauth/authorize/approve      (admin-only, allow/deny)
  - POST /oauth/token                  (authorization_code + refresh_token)
  - Refresh-token rotation + reuse (theft) detection
  - MCP endpoint (routers/mcp_ora_chat.py) accepting an OAuth access
    token, on top of the pre-existing Bearer admin-key path (which
    test_mcp_ora_chat_admin.py already covers and must keep passing).
  - Backend-native discovery fallback
    (GET /mcp/.well-known/oauth-authorization-server,
     GET /mcp/ora-chat/.well-known/oauth-protected-resource).

Uses requests against the running backend (BASE_URL, default
http://localhost:8001 — see conftest.py) exactly like
test_mcp_ora_chat_admin.py.
"""
import os
import random

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")
API = f"{BASE_URL}/api/aurem-dev"
MCP_URL = f"{API}/mcp/ora-chat"
RESOURCE = MCP_URL

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


def _pkce_pair():
    import base64
    import hashlib
    import secrets
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest(),
    ).rstrip(b"=").decode()
    return verifier, challenge


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
    }, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def nonadmin_token():
    email = f"pytest-oauth-nonadmin-{random.randint(100000, 999999)}@example.com"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": "NonAdmin2026!", "name": "N",
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def dcr_client():
    r = requests.post(f"{API}/oauth/register", json={
        "client_name": "pytest OAuth client",
        "redirect_uris": ["http://localhost:9999/callback"],
    }, timeout=15)
    assert r.status_code == 201, r.text
    data = r.json()
    yield data["client_id"], "http://localhost:9999/callback"


def _start_authorize(client_id, redirect_uri, resource=RESOURCE, state="s1"):
    verifier, challenge = _pkce_pair()
    r = requests.get(f"{API}/oauth/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": resource, "state": state, "scope": "mcp",
    }, timeout=15, allow_redirects=False)
    return r, verifier


class TestDynamicClientRegistration:
    def test_register_returns_public_client(self, dcr_client):
        client_id, redirect_uri = dcr_client
        assert client_id

    def test_register_rejects_non_https_non_localhost_redirect(self):
        r = requests.post(f"{API}/oauth/register", json={
            "client_name": "evil", "redirect_uris": ["http://evil.example/cb"],
        }, timeout=10)
        assert r.status_code == 400

    def test_register_rejects_missing_redirect_uris(self):
        r = requests.post(f"{API}/oauth/register", json={"client_name": "no-redirects"}, timeout=10)
        assert r.status_code in (400, 422)


class TestAuthorizeEndpoint:
    def test_valid_request_redirects_to_frontend_consent(self, dcr_client):
        client_id, redirect_uri = dcr_client
        r, _ = _start_authorize(client_id, redirect_uri)
        assert r.status_code in (302, 307)
        assert "/oauth/authorize?consent_id=" in r.headers["location"]

    def test_unknown_client_rejected(self):
        r, _ = _start_authorize("does-not-exist", "http://localhost:9999/callback")
        assert r.status_code == 400

    def test_redirect_uri_not_registered_rejected(self, dcr_client):
        client_id, _ = dcr_client
        r, _ = _start_authorize(client_id, "http://localhost:9999/wrong-path")
        assert r.status_code == 400

    def test_wrong_resource_redirects_with_invalid_target(self, dcr_client):
        client_id, redirect_uri = dcr_client
        r, _ = _start_authorize(client_id, redirect_uri, resource="https://evil.example/steal")
        assert r.status_code in (302, 307)
        assert "error=invalid_target" in r.headers["location"]


class TestConsentAdminOnly:
    def test_pending_requires_auth(self, dcr_client):
        client_id, redirect_uri = dcr_client
        r, _ = _start_authorize(client_id, redirect_uri)
        consent_id = r.headers["location"].split("consent_id=")[1]
        r2 = requests.get(f"{API}/oauth/pending/{consent_id}", timeout=10)
        assert r2.status_code == 401

    def test_nonadmin_cannot_view_or_approve_pending(self, dcr_client, nonadmin_token):
        client_id, redirect_uri = dcr_client
        r, _ = _start_authorize(client_id, redirect_uri)
        consent_id = r.headers["location"].split("consent_id=")[1]
        headers = {"Authorization": f"Bearer {nonadmin_token}"}
        r2 = requests.get(f"{API}/oauth/pending/{consent_id}", headers=headers, timeout=10)
        assert r2.status_code == 403
        r3 = requests.post(f"{API}/oauth/authorize/approve",
                            json={"consent_id": consent_id, "decision": "allow"},
                            headers=headers, timeout=10)
        assert r3.status_code == 403

    def test_admin_can_view_pending(self, dcr_client, admin_token):
        client_id, redirect_uri = dcr_client
        r, _ = _start_authorize(client_id, redirect_uri)
        consent_id = r.headers["location"].split("consent_id=")[1]
        headers = {"Authorization": f"Bearer {admin_token}"}
        r2 = requests.get(f"{API}/oauth/pending/{consent_id}", headers=headers, timeout=10)
        assert r2.status_code == 200
        assert r2.json()["client_name"] == "pytest OAuth client"

    def test_expired_or_unknown_consent_id_404s(self, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        r = requests.get(f"{API}/oauth/pending/does-not-exist", headers=headers, timeout=10)
        assert r.status_code == 404


def _full_authorize_flow(dcr_client, admin_token, decision="allow"):
    client_id, redirect_uri = dcr_client
    r, verifier = _start_authorize(client_id, redirect_uri)
    consent_id = r.headers["location"].split("consent_id=")[1]
    headers = {"Authorization": f"Bearer {admin_token}"}
    approve = requests.post(f"{API}/oauth/authorize/approve",
                             json={"consent_id": consent_id, "decision": decision},
                             headers=headers, timeout=10)
    assert approve.status_code == 200, approve.text
    return approve.json()["redirect_url"], verifier, client_id, redirect_uri


class TestApproveAndTokenExchange:
    def test_deny_redirects_with_access_denied(self, dcr_client, admin_token):
        redirect_url, _, _, _ = _full_authorize_flow(dcr_client, admin_token, decision="deny")
        assert "error=access_denied" in redirect_url

    def test_allow_then_exchange_code_for_tokens(self, dcr_client, admin_token):
        from urllib.parse import parse_qs, urlparse
        redirect_url, verifier, client_id, redirect_uri = _full_authorize_flow(dcr_client, admin_token)
        q = parse_qs(urlparse(redirect_url).query)
        assert q["state"][0] == "s1"
        code = q["code"][0]
        r = requests.post(f"{API}/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": client_id, "code_verifier": verifier, "resource": RESOURCE,
        }, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"].startswith("oraoauth_at_")
        assert body["refresh_token"].startswith("oraoauth_rt_")
        assert body["token_type"] == "Bearer"

    def test_code_is_single_use(self, dcr_client, admin_token):
        from urllib.parse import parse_qs, urlparse
        redirect_url, verifier, client_id, redirect_uri = _full_authorize_flow(dcr_client, admin_token)
        code = parse_qs(urlparse(redirect_url).query)["code"][0]
        body = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "client_id": client_id, "code_verifier": verifier, "resource": RESOURCE}
        r1 = requests.post(f"{API}/oauth/token", data=body, timeout=15)
        assert r1.status_code == 200
        r2 = requests.post(f"{API}/oauth/token", data=body, timeout=15)
        assert r2.status_code == 400

    def test_wrong_pkce_verifier_rejected(self, dcr_client, admin_token):
        from urllib.parse import parse_qs, urlparse
        redirect_url, _verifier, client_id, redirect_uri = _full_authorize_flow(dcr_client, admin_token)
        code = parse_qs(urlparse(redirect_url).query)["code"][0]
        r = requests.post(f"{API}/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": client_id, "code_verifier": "wrong-verifier-entirely", "resource": RESOURCE,
        }, timeout=15)
        assert r.status_code == 400


@pytest.fixture()
def oauth_tokens(dcr_client, admin_token):
    from urllib.parse import parse_qs, urlparse
    redirect_url, verifier, client_id, redirect_uri = _full_authorize_flow(dcr_client, admin_token)
    code = parse_qs(urlparse(redirect_url).query)["code"][0]
    r = requests.post(f"{API}/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": verifier, "resource": RESOURCE,
    }, timeout=15)
    assert r.status_code == 200
    body = r.json()
    return body["access_token"], body["refresh_token"], client_id


class TestMcpWithOAuthToken:
    def test_tools_list_with_oauth_access_token(self, oauth_tokens):
        access, _refresh, _client_id = oauth_tokens
        r = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                           headers={"Authorization": f"Bearer {access}"}, timeout=10)
        assert r.status_code == 200
        assert len(r.json()["result"]["tools"]) == 3

    def test_missing_auth_returns_401_with_www_authenticate_hint(self):
        r = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=10)
        assert r.status_code == 401
        hint = r.headers.get("www-authenticate", "")
        assert "resource_metadata=" in hint
        assert "/.well-known/oauth-protected-resource/api/aurem-dev/mcp/ora-chat" in hint


class TestRefreshRotation:
    def test_refresh_issues_new_pair(self, oauth_tokens):
        _access, refresh, client_id = oauth_tokens
        r = requests.post(f"{API}/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id,
        }, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"].startswith("oraoauth_at_")
        assert body["refresh_token"] != refresh

    def test_reusing_old_refresh_token_fails_and_kills_family(self, oauth_tokens):
        access, refresh, client_id = oauth_tokens
        r1 = requests.post(f"{API}/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id,
        }, timeout=15)
        assert r1.status_code == 200
        new_access = r1.json()["access_token"]

        # Reusing the already-rotated-away token must fail...
        r2 = requests.post(f"{API}/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id,
        }, timeout=15)
        assert r2.status_code == 400

        # ...and the fresh access token issued from that SAME family
        # must now be revoked too (theft-mitigation kills the family).
        r3 = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                            headers={"Authorization": f"Bearer {new_access}"}, timeout=10)
        assert r3.status_code == 403


class TestDiscoveryFallbacks:
    def test_backend_native_as_metadata(self):
        r = requests.get(f"{API}/mcp/.well-known/oauth-authorization-server", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["issuer"].endswith("/api/aurem-dev/mcp")
        assert "S256" in body["code_challenge_methods_supported"]
        assert body["registration_endpoint"].endswith("/oauth/register")

    def test_backend_native_protected_resource_metadata(self):
        r = requests.get(f"{MCP_URL}/.well-known/oauth-protected-resource", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["resource"].endswith("/api/aurem-dev/mcp/ora-chat")

    def test_manifest_mentions_oauth(self):
        r = requests.get(MCP_URL, timeout=10)
        assert r.status_code == 200
        assert "oauth" in r.json()["auth"]["type"].lower()
