"""
test_regression_iter403_oauth_open_redirect_and_token_binding.py
====================================================================

Root cause (real High-severity finding, manual code-read security
audit, 2026-09-30): routers/oauth.py's redirect_uri validation only
ever ran `if dcr_client and redirect_uri not in dcr_client[...]` — i.e.
ONLY when a DCR record for client_id happened to exist. Any client_id
with NO registration on file (a made-up value, or a client_id an
attacker self-registers for a DIFFERENT resource) sailed through with
the redirect_uri completely unchecked.

Attack: attacker builds
  /oauth/authorize?client_id=x&redirect_uri=https://evil.example/cb&code_challenge=<attacker's own>
Victim authenticates on AUREM's real login page (genuine TLS cert,
genuine domain — nothing about the page itself looks wrong). The
browser is 302'd to the attacker's redirect_uri with the auth code.
PKCE does not help here — code_challenge/verifier are a matched pair
the ATTACKER generated; the code just needs to reach their own server,
which it now does. /oauth/token then didn't check that the client_id
presented there matched the one the code was issued to, so the
attacker could exchange the code with any client_id.

If the victim is an ORA admin and the flow targets the ORA Chat MCP
resource, this hands the attacker an admin-scoped OAuth token — full
tool execution (repo commits, bash) as that admin.

Fix (this PR): every redirect_uri is now checked against either (a)
the fixed, Anthropic-owned claude.ai callback host, or (b) an exact,
constant-time match against THIS client_id's own DCR-registered
redirect_uris. No match → local 400, never a redirect (never send the
browser anywhere we haven't verified). /oauth/authorize/deny gets the
same check (it was a pure open redirect before — zero validation at
all). /oauth/token now requires client_id + redirect_uri and matches
both, exactly, against the code's own record.

This file complements (does not duplicate) the ORA-chat-resource-
specific coverage already added to tests/test_oauth_ora_chat.py.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")
API = f"{BASE_URL}/api/aurem-dev"


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest(),
    ).rstrip(b"=").decode()
    return verifier, challenge


def _fresh_user():
    email = f"pytest-oauth401-{secrets.token_hex(6)}@example.com"
    password = "Oauth401Pass!2026"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password, "name": "OAuth401",
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    return email, password


@pytest.fixture()
def registered_client():
    r = requests.post(f"{API}/oauth/register", json={
        "client_name": "pytest iter401 legit client",
        "redirect_uris": ["https://legit-app.example.com/callback"],
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()["client_id"], "https://legit-app.example.com/callback"


class TestUnregisteredClientCannotStealCode:
    """The exact attack in the audit: a client_id no one issued, a
    redirect_uri the attacker controls, general (non-admin) resource."""

    def test_get_authorize_rejects_unregistered_client_arbitrary_redirect(self):
        _, challenge = _pkce_pair()
        r = requests.get(f"{API}/oauth/authorize", params={
            "response_type": "code",
            "client_id": "attacker-made-up-client-id",
            "redirect_uri": "https://evil.example/cb",
            "code_challenge": challenge, "code_challenge_method": "S256",
        }, timeout=10)
        assert r.status_code == 400
        assert "code=" not in r.text

    def test_post_authorize_rejects_even_with_correct_credentials(self):
        """Defense in depth: even if the attacker somehow gets a real
        user to POST credentials directly (bypassing the GET page),
        the server-side check must still block the redirect."""
        email, password = _fresh_user()
        _, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": "attacker-made-up-client-id-2",
            "redirect_uri": "https://evil.example/cb2",
            "code_challenge": challenge, "code_challenge_method": "S256",
        }
        r = requests.post(f"{API}/oauth/authorize", params=params,
                           data={"email": email, "password": password},
                           timeout=15, allow_redirects=False)
        assert r.status_code == 400


class TestDcrRegisteredClientRedirectMustMatchExactly:
    def test_registered_redirect_uri_still_works(self, registered_client):
        client_id, redirect_uri = registered_client
        email, password = _fresh_user()
        verifier, challenge = _pkce_pair()
        params = {
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }
        get_r = requests.get(f"{API}/oauth/authorize", params=params, timeout=10)
        assert get_r.status_code == 200
        post_r = requests.post(f"{API}/oauth/authorize", params=params,
                                data={"email": email, "password": password},
                                timeout=15, allow_redirects=False)
        assert post_r.status_code == 302
        q = parse_qs(urlparse(post_r.headers["location"]).query)
        assert "code" in q
        code = q["code"][0]

        tok = requests.post(f"{API}/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": client_id, "code_verifier": verifier,
        }, timeout=15)
        assert tok.status_code == 200, tok.text

    def test_registered_client_with_a_different_unregistered_redirect_rejected(self, registered_client):
        """The registered client_id is real, but this specific
        redirect_uri was never registered for it — must still reject."""
        client_id, _real_redirect = registered_client
        _, challenge = _pkce_pair()
        r = requests.get(f"{API}/oauth/authorize", params={
            "response_type": "code", "client_id": client_id,
            "redirect_uri": "https://attacker-controlled.example/cb",
            "code_challenge": challenge, "code_challenge_method": "S256",
        }, timeout=10)
        assert r.status_code == 400


class TestLocalhostUserinfoBypassFixed:
    """The Low-severity finding: _valid_redirect_uri used
    `.startswith("http://localhost:")`, which a URL like
    `http://localhost:1@evil.example/` also starts with — but every
    real URL parser (and every browser) resolves that URL's actual
    host to `evil.example` (`localhost:1` is HTTP Basic-Auth userinfo,
    not the host). Fixed by parsing with urllib.parse and checking
    .hostname, never a string prefix."""

    def test_userinfo_at_trick_does_not_bypass_localhost_allowlist(self):
        _, challenge = _pkce_pair()
        r = requests.get(f"{API}/oauth/authorize", params={
            "response_type": "code",
            "client_id": "attacker-userinfo-trick-client",
            "redirect_uri": "http://localhost:1@evil.example/cb",
            "code_challenge": challenge, "code_challenge_method": "S256",
        }, timeout=10)
        assert r.status_code == 400

    def test_registration_also_rejects_the_same_trick(self):
        r = requests.post(f"{API}/oauth/register", json={
            "client_name": "userinfo-trick-at-registration",
            "redirect_uris": ["http://localhost:1@evil.example/cb"],
        }, timeout=10)
        assert r.status_code == 400

    def test_real_bare_localhost_redirect_still_allowed_at_registration(self):
        """Regression: fixing the bypass must not break genuinely
        local dev clients (e.g. a CLI tool on http://localhost:9999/cb)."""
        r = requests.post(f"{API}/oauth/register", json={
            "client_name": "genuine-local-dev-client",
            "redirect_uris": ["http://localhost:54321/cb"],
        }, timeout=10)
        assert r.status_code == 201, r.text


class TestDenyEndpointNoLongerOpenRedirect:
    """/oauth/authorize/deny previously did zero validation — pure
    open redirect. An attacker's phishing link could point the "Cancel"
    button anywhere; the victim's browser would be sent to that URL
    with AUREM's own domain as the referrer, right after typing a
    password into AUREM's own real login form."""

    def test_deny_rejects_unregistered_redirect(self):
        r = requests.post(f"{API}/oauth/authorize/deny", params={
            "client_id": "attacker-deny-client",
            "redirect_uri": "https://evil.example/deny-landing",
            "state": "xyz",
        }, timeout=10, allow_redirects=False)
        assert r.status_code == 400

    def test_deny_still_works_for_the_real_claude_callback(self):
        r = requests.post(f"{API}/oauth/authorize/deny", params={
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "state": "xyz2",
        }, timeout=10, allow_redirects=False)
        assert r.status_code == 302
        assert "error=access_denied" in r.headers["location"]

    def test_deny_still_works_for_a_registered_client(self, registered_client):
        client_id, redirect_uri = registered_client
        r = requests.post(f"{API}/oauth/authorize/deny", params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": "xyz3",
        }, timeout=10, allow_redirects=False)
        assert r.status_code == 302
        assert "error=access_denied" in r.headers["location"]


class TestConsentPageShowsRealIdentity:
    """Points 3 of the audit fix: the consent page used to render the
    raw, attacker-controlled client_id string with no indication of
    where the browser would be sent back to. It must now show the
    registered client_name and the actual redirect host."""

    def test_consent_page_shows_registered_client_name_not_raw_id(self, registered_client):
        client_id, redirect_uri = registered_client
        _, challenge = _pkce_pair()
        r = requests.get(f"{API}/oauth/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }, timeout=10)
        assert r.status_code == 200
        assert "pytest iter401 legit client" in r.text
        # client_id legitimately still appears once, in the hidden
        # form field the POST needs to resubmit it — but the
        # user-visible "is requesting access" line must show the
        # friendly name, not the raw id, which is what this test
        # guards (see data-testid="oauth-client-name" below).
        assert f'data-testid="oauth-client-name">{"pytest iter401 legit client"}<' in r.text

    def test_consent_page_shows_redirect_host(self, registered_client):
        client_id, redirect_uri = registered_client
        _, challenge = _pkce_pair()
        r = requests.get(f"{API}/oauth/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }, timeout=10)
        assert r.status_code == 200
        assert "legit-app.example.com" in r.text
