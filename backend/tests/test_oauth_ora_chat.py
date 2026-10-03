"""
tests/test_oauth_ora_chat.py — 2026-09-30 OAuth 2.1 for the shared
authorization server (routers/oauth.py), covering the admin-only ORA
Chat MCP resource specifically (production incident: Claude couldn't
connect — .well-known/* returned the frontend's HTML shell instead of
JSON, and the AS metadata had no registration_endpoint. Fixed by:
(1) nginx/Vite proxying bare .well-known/* paths to the backend
instead of the SPA, (2) adding RFC 7591 Dynamic Client Registration +
a registration_endpoint field, (3) admin-only gating in
/oauth/authorize specifically for the ORA Chat MCP `resource`).

Does NOT re-test the general MCP "Claude Directory" resource's own
existing behavior beyond the one regression check below (that this
work didn't accidentally admin-gate it too).

Regression coverage for:
  - GET  /.well-known/oauth-authorization-server  (registration_endpoint present)
  - GET  /.well-known/oauth-protected-resource      (correct resource/authorization_servers)
  - POST /oauth/register                            (DCR, redirect_uri validation)
  - GET  /oauth/authorize                           (renders login form)
  - POST /oauth/authorize                           (admin-only for ORA Chat MCP resource;
                                                       still open for other/no resource — regression)
  - POST /oauth/token                               (PKCE, single-use code)
  - MCP endpoint accepting the resulting token
  - WWW-Authenticate 401 hint → bare-root PRM URL
"""
import base64
import hashlib
import os
import random
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")
API = f"{BASE_URL}/api/aurem-dev"
MCP_URL = f"{API}/mcp/ora-chat"
RESOURCE = MCP_URL

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"

# 2026-09-30 — SECURITY FIX regression coverage update: the ORA Chat MCP
# resource now ONLY accepts the real, Anthropic-owned claude.ai callback
# for its redirect_uri (see routers/oauth.py LEGACY_CLAUDE_REDIRECT_HOST /
# _validate_redirect_uri_for_client) — DCR registration alone is no
# longer sufficient for THIS specific resource, even for a client that
# legitimately registered some other https/localhost redirect_uri. Every
# test below that authorizes against the ORA Chat resource now uses this
# constant instead of the generic dcr_client fixture's redirect_uri.
CLAUDE_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest(),
    ).rstrip(b"=").decode()
    return verifier, challenge


@pytest.fixture(scope="module")
def dcr_client():
    r = requests.post(f"{API}/oauth/register", json={
        "client_name": "pytest unified OAuth client",
        "redirect_uris": ["http://localhost:9999/callback"],
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()["client_id"], "http://localhost:9999/callback"


def _fresh_nonadmin_creds():
    """Not a fixture — deliberately called directly inside a test body
    (not via a module-scoped fixture). conftest.py's autouse
    `clean_signup_rate_limit` wipes dev_users rows created from test
    IPs BEFORE every test; a module-scoped fixture set up once and
    reused across tests races with that wipe. Creating-then-
    immediately-using within the SAME test body sidesteps it."""
    email = f"pytest-oauth-unified-nonadmin-{random.randint(100000, 999999)}@example.com"
    password = "NonAdmin2026!"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password, "name": "N",
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    return email, password


class TestDiscovery:
    def test_as_metadata_has_registration_endpoint(self):
        r = requests.get(f"{API}/.well-known/oauth-authorization-server", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["issuer"].endswith("/api/aurem-dev")
        assert body["registration_endpoint"].endswith("/oauth/register")
        assert "S256" in body["code_challenge_methods_supported"]

    def test_protected_resource_metadata(self):
        r = requests.get(f"{API}/.well-known/oauth-protected-resource", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["resource"].endswith("/api/aurem-dev/mcp/ora-chat")
        assert body["authorization_servers"][0].endswith("/api/aurem-dev")


class TestDynamicClientRegistration:
    def test_register_returns_client_id(self, dcr_client):
        client_id, _ = dcr_client
        assert client_id

    def test_rejects_non_https_non_localhost_redirect(self):
        r = requests.post(f"{API}/oauth/register", json={
            "client_name": "evil", "redirect_uris": ["http://evil.example/cb"],
        }, timeout=10)
        assert r.status_code == 400

    def test_rejects_missing_redirect_uris(self):
        r = requests.post(f"{API}/oauth/register", json={"client_name": "no-redirects"}, timeout=10)
        assert r.status_code == 400


def _authorize_and_login(client_id, redirect_uri, email, password, resource=RESOURCE, state="s1"):
    verifier, challenge = _pkce_pair()
    params = {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": resource, "state": state, "scope": "mcp",
    }
    get_r = requests.get(f"{API}/oauth/authorize", params=params, timeout=15)
    assert get_r.status_code == 200
    post_r = requests.post(f"{API}/oauth/authorize", params=params,
                            data={"email": email, "password": password},
                            timeout=15, allow_redirects=False)
    return post_r, verifier


class TestAuthorizeAdminGating:
    def test_get_authorize_renders_login_form(self, dcr_client):
        client_id, _redirect_uri = dcr_client
        _, challenge = _pkce_pair()
        r = requests.get(f"{API}/oauth/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_REDIRECT_URI,
            "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE,
        }, timeout=10)
        assert r.status_code == 200
        assert "oauth-login-form" in r.text

    def test_wrong_password_redirects_with_invalid_credentials(self, dcr_client):
        client_id, _redirect_uri = dcr_client
        r, _ = _authorize_and_login(client_id, CLAUDE_REDIRECT_URI, ADMIN_EMAIL, "wrong-password-entirely")
        assert r.status_code == 302
        assert "error=invalid_credentials" in r.headers["location"]

    def test_admin_login_for_ora_chat_resource_issues_code(self, dcr_client):
        client_id, _redirect_uri = dcr_client
        r, _ = _authorize_and_login(client_id, CLAUDE_REDIRECT_URI, ADMIN_EMAIL, ADMIN_PASSWORD)
        assert r.status_code == 302
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert "code" in q
        assert q["state"][0] == "s1"

    def test_nonadmin_login_for_ora_chat_resource_rejected(self, dcr_client):
        client_id, _redirect_uri = dcr_client
        email, password = _fresh_nonadmin_creds()
        r, _ = _authorize_and_login(client_id, CLAUDE_REDIRECT_URI, email, password)
        assert r.status_code == 302
        assert "error=admin_required" in r.headers["location"]
        assert "code=" not in r.headers["location"]

    def test_nonadmin_login_for_OTHER_resource_still_allowed(self, dcr_client):
        """Regression: the general MCP 'Claude Directory' resource
        (or no resource at all) must stay open to any dev_user —
        admin-gating here is scoped ONLY to the ORA Chat MCP
        resource, not the whole shared OAuth server. Not the claude.ai-
        only resource, so the client's own registered (localhost)
        redirect_uri is still correctly accepted here."""
        client_id, redirect_uri = dcr_client
        email, password = _fresh_nonadmin_creds()
        r, _ = _authorize_and_login(client_id, redirect_uri, email, password,
                                     resource=f"{BASE_URL}/some/other/resource", state="s2")
        assert r.status_code == 302
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert "code" in q

    def test_non_claude_redirect_rejected_for_ora_chat_even_if_dcr_registered(self, dcr_client):
        """2026-09-30 SECURITY FIX: the ORA Chat resource used to accept
        ANY redirect_uri the client had registered via DCR. Now it must
        reject even a client's OWN correctly-registered redirect_uri if
        its host isn't claude.ai — this is the actual High-severity fix
        (an attacker can self-register a client the normal way and
        still not get an ORA-chat admin auth code redirected anywhere
        but claude.ai)."""
        client_id, redirect_uri = dcr_client  # redirect_uri == "http://localhost:9999/callback", genuinely registered
        r = requests.get(f"{API}/oauth/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "code_challenge": _pkce_pair()[1], "code_challenge_method": "S256", "resource": RESOURCE,
        }, timeout=10)
        assert r.status_code == 400

    def test_unregistered_client_and_unregistered_redirect_rejected(self):
        """The actual attack described in the audit: an attacker who
        never registered anything, using a client_id no one issued and
        a redirect_uri they control, must be rejected outright — not
        silently allowed through because "no DCR record exists"."""
        r = requests.get(f"{API}/oauth/authorize", params={
            "response_type": "code", "client_id": "attacker-controlled-client-id-12345",
            "redirect_uri": "https://evil.example/cb",
            "code_challenge": _pkce_pair()[1], "code_challenge_method": "S256", "resource": RESOURCE,
        }, timeout=10)
        assert r.status_code == 400



class TestTokenExchange:
    def test_full_exchange_and_single_use_code(self, dcr_client):
        client_id, _redirect_uri = dcr_client
        r, verifier = _authorize_and_login(client_id, CLAUDE_REDIRECT_URI, ADMIN_EMAIL, ADMIN_PASSWORD, state="s3")
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        body = {"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_REDIRECT_URI,
                "client_id": client_id, "code_verifier": verifier}
        tok = requests.post(f"{API}/oauth/token", data=body, timeout=15)
        assert tok.status_code == 200, tok.text
        token_body = tok.json()
        assert token_body["access_token"].startswith("sk-aurem-oauth-")
        assert token_body["token_type"] == "Bearer"

        # Reusing the same code must fail.
        tok2 = requests.post(f"{API}/oauth/token", data=body, timeout=15)
        assert tok2.status_code == 400

    def test_wrong_pkce_verifier_rejected(self, dcr_client):
        client_id, _redirect_uri = dcr_client
        r, _verifier = _authorize_and_login(client_id, CLAUDE_REDIRECT_URI, ADMIN_EMAIL, ADMIN_PASSWORD, state="s4")
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        tok = requests.post(f"{API}/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_REDIRECT_URI,
            "client_id": client_id, "code_verifier": "totally-wrong-verifier",
        }, timeout=15)
        assert tok.status_code == 400

    def test_token_exchange_rejects_client_id_mismatch(self, dcr_client):
        """2026-09-30 SECURITY FIX: /oauth/token used to accept any
        client_id (or none) at the token step, never checking it
        against the client_id the code was actually issued to."""
        client_id, _redirect_uri = dcr_client
        r, verifier = _authorize_and_login(client_id, CLAUDE_REDIRECT_URI, ADMIN_EMAIL, ADMIN_PASSWORD, state="s6")
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        tok = requests.post(f"{API}/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_REDIRECT_URI,
            "client_id": "a-totally-different-client-id", "code_verifier": verifier,
        }, timeout=15)
        assert tok.status_code == 400
        assert "client_id" in tok.text

    def test_token_exchange_rejects_redirect_uri_mismatch(self, dcr_client):
        """2026-09-30 SECURITY FIX: /oauth/token used to skip the
        redirect_uri check entirely whenever the caller simply omitted
        the field."""
        client_id, _redirect_uri = dcr_client
        r, verifier = _authorize_and_login(client_id, CLAUDE_REDIRECT_URI, ADMIN_EMAIL, ADMIN_PASSWORD, state="s7")
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        # Omitting redirect_uri must now be a hard 400 (missing param),
        # not a silently-skipped check.
        tok = requests.post(f"{API}/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": client_id, "code_verifier": verifier,
        }, timeout=15)
        assert tok.status_code == 400
        assert "redirect_uri" in tok.text


@pytest.fixture()
def ora_chat_oauth_token(dcr_client):
    client_id, _redirect_uri = dcr_client
    r, verifier = _authorize_and_login(client_id, CLAUDE_REDIRECT_URI, ADMIN_EMAIL, ADMIN_PASSWORD, state="s5")
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = requests.post(f"{API}/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_REDIRECT_URI,
        "client_id": client_id, "code_verifier": verifier,
    }, timeout=15)
    assert tok.status_code == 200
    return tok.json()["access_token"]


class TestMcpWithOAuthToken:
    def test_tools_list_with_oauth_token(self, ora_chat_oauth_token):
        r = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                           headers={"Authorization": f"Bearer {ora_chat_oauth_token}"}, timeout=10)
        assert r.status_code == 200
        assert len(r.json()["result"]["tools"]) == 3

    def test_401_www_authenticate_points_to_bare_root_prm(self):
        r = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=10)
        assert r.status_code == 401
        hint = r.headers.get("www-authenticate", "")
        assert "resource_metadata=" in hint
        assert hint.rstrip('"').endswith("/.well-known/oauth-protected-resource")
