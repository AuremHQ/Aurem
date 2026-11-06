"""tests/test_regression_iter2026_63_oauth_redirect_allowlist.py

Security audit Phase 1: OAuth redirect_uri validation.

The holes this closes (routers/oauth.py):
  (a) a legacy / hand-configured client_id (the "Claude Directory"
      connector) skipped the redirect_uri check entirely, so an attacker
      could pick their own redirect_uri and their own PKCE challenge and
      redeem the victim's auth code later;
  (b) POST /oauth/authorize/deny redirected to ANY redirect_uri;
  (c) GET /oauth/authorize rendered the login form for any redirect_uri;
  (d) `_valid_redirect_uri` used string-prefix checks, so
      `http://localhost:1@evil.example/` passed as "localhost".

Everything runs in-process against a fake Mongo, so these tests do not
need the live backend.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
from datetime import UTC, datetime, timedelta

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import oauth

BASE = "/api/aurem-dev"
# The Claude connector callback documented in memory/PRD.md; it is the
# code default of OAUTH_LEGACY_REDIRECT_ALLOWLIST.
LEGACY_OK = "https://claude.ai/api/mcp/auth_callback"
EVIL = "https://evil.example/cb"
PASSWORD = "CorrectHorse-Battery9"


# ── fake Mongo ────────────────────────────────────────────────────────
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    @staticmethod
    def _match(row: dict, flt: dict) -> bool:
        for k, v in flt.items():
            if isinstance(v, dict):
                return False  # operator objects never match a plain value
            if row.get(k) != v:
                return False
        return True

    async def find_one(self, flt, projection=None):
        for r in self.rows:
            if self._match(r, flt):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def update_one(self, flt, upd):
        for r in self.rows:
            if self._match(r, flt):
                r.update(upd.get("$set", {}))
                return


class _FakeDB:
    def __init__(self):
        self.oauth_dcr_clients = _Coll()
        self.oauth_codes = _Coll()
        self.api_keys = _Coll()
        self.dev_users = _Coll()


@pytest.fixture
def db(monkeypatch):
    d = _FakeDB()
    d.dev_users.rows.append({
        "user_id": "u1", "email": "victim@example.com", "is_admin": False, "tier": "pro",
        "password": bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode(),
    })
    monkeypatch.setattr(oauth, "get_db", lambda: d)
    monkeypatch.delenv("OAUTH_LEGACY_REDIRECT_ALLOWLIST", raising=False)
    return d


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(oauth.router, prefix=BASE)
    return TestClient(app, follow_redirects=False)


def _challenge() -> str:
    digest = hashlib.sha256(secrets.token_urlsafe(32).encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _params(client_id, redirect_uri, **extra):
    p = {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": "mcp", "state": "st", "code_challenge": _challenge(),
        "code_challenge_method": "S256",
    }
    p.update(extra)
    return p


def _register(db, uris, client_id="dcr-client-1"):
    db.oauth_dcr_clients.rows.append({"client_id": client_id, "client_name": "T", "redirect_uris": list(uris)})
    return client_id


def _get(client, cid, uri):
    return client.get(f"{BASE}/oauth/authorize", params=_params(cid, uri))


def _post(client, cid, uri, password=PASSWORD):
    return client.post(
        f"{BASE}/oauth/authorize", params=_params(cid, uri),
        data={"email": "victim@example.com", "password": password},
    )


def _deny(client, cid, uri):
    return client.post(f"{BASE}/oauth/authorize/deny", params={"client_id": cid, "redirect_uri": uri, "state": "st"})


# ── (a) legacy client + attacker redirect ─────────────────────────────
def test_legacy_client_get_with_evil_redirect_is_rejected(client):
    r = _get(client, "claude-desktop", EVIL)
    assert r.status_code == 400
    assert "location" not in r.headers
    assert "evil.example" not in r.text


def test_legacy_client_post_with_evil_redirect_issues_no_code(client, db):
    r = _post(client, "claude-desktop", EVIL)
    assert r.status_code == 400
    assert "location" not in r.headers
    assert db.oauth_codes.rows == []
    assert "evil.example" not in r.text


def test_legacy_client_with_allowlisted_redirect_works(client, db):
    assert _get(client, "claude-desktop", LEGACY_OK).status_code == 200
    r = _post(client, "claude-desktop", LEGACY_OK)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(LEGACY_OK + "?code=")
    assert len(db.oauth_codes.rows) == 1


# ── (b) deny endpoint ─────────────────────────────────────────────────
def test_deny_with_evil_redirect_is_rejected(client):
    r = _deny(client, "claude-desktop", EVIL)
    assert r.status_code == 400
    assert "location" not in r.headers


def test_deny_requires_client_id(client):
    r = client.post(f"{BASE}/oauth/authorize/deny", params={"redirect_uri": LEGACY_OK, "state": "s"})
    assert r.status_code == 400
    assert "location" not in r.headers


def test_deny_with_valid_pair_redirects_with_access_denied(client):
    r = _deny(client, "claude-desktop", LEGACY_OK)
    assert r.status_code == 302
    assert r.headers["location"].startswith(LEGACY_OK + "?error=access_denied")


# ── DCR clients ───────────────────────────────────────────────────────
def test_dcr_client_exact_registered_uri_works(client, db):
    cid = _register(db, ["https://app.example.org/cb"])
    assert _get(client, cid, "https://app.example.org/cb").status_code == 200
    r = _post(client, cid, "https://app.example.org/cb")
    assert r.status_code == 302 and r.headers["location"].startswith("https://app.example.org/cb?code=")


def test_dcr_client_unregistered_https_uri_is_rejected_everywhere(client, db):
    cid = _register(db, ["https://app.example.org/cb"])
    assert _get(client, cid, EVIL).status_code == 400
    assert _post(client, cid, EVIL).status_code == 400
    assert _deny(client, cid, EVIL).status_code == 400
    assert db.oauth_codes.rows == []


def test_dcr_client_cannot_use_the_legacy_allowlist_uri(client, db):
    cid = _register(db, ["https://app.example.org/cb"])
    assert _get(client, cid, LEGACY_OK).status_code == 400


# ── redirect-URI parser tricks (rejected for the legacy client) ───────
TRICKS = [
    LEGACY_OK + "/",                                  # trailing slash
    LEGACY_OK + "?x=1",                               # appended query
    LEGACY_OK + "#frag",                              # fragment
    "https://CLAUDE.AI/api/mcp/auth_callback",        # uppercase host
    "https://claude.ai@evil.example/cb",              # userinfo
    "https://claude.ai:443/api/mcp/auth_callback",    # explicit port
    "https://claude.ai.evil.example/api/mcp/auth_callback",
    "https://clаude.ai/api/mcp/auth_callback",   # Cyrillic a (homograph)
    "https://xn--clude-9ua.ai/api/mcp/auth_callback",  # punycode lookalike
    "https://claude.ai%2f.evil.example/api/mcp/auth_callback",
    "https://claude.ai%252f@evil.example/",           # double-encoded
    "http://claude.ai/api/mcp/auth_callback",         # downgraded scheme
    "javascript:alert(1)",
    "//evil.example/cb",                              # protocol-relative
    "data:text/html,<script>1</script>",
    "https://claude.ai/api/mcp/auth_callback\r\nSet-Cookie: x=1",
    " " + LEGACY_OK,
    "",
]


@pytest.mark.parametrize("bad_uri", TRICKS)
def test_uri_variants_are_rejected_for_legacy_client(client, db, bad_uri):
    for resp in (_get(client, "claude-desktop", bad_uri),
                 _post(client, "claude-desktop", bad_uri),
                 _deny(client, "claude-desktop", bad_uri)):
        assert resp.status_code == 400, (bad_uri, resp.status_code)
        assert "location" not in resp.headers
    assert db.oauth_codes.rows == []


# ── env-configurable allowlist ────────────────────────────────────────
def test_env_allowlist_replaces_default_and_is_exact(client, monkeypatch):
    monkeypatch.setenv("OAUTH_LEGACY_REDIRECT_ALLOWLIST", "https://a.example/cb, https://b.example/cb")
    assert _get(client, "claude-desktop", "https://a.example/cb").status_code == 200
    assert _get(client, "claude-desktop", "https://b.example/cb").status_code == 200
    assert _get(client, "claude-desktop", LEGACY_OK).status_code == 400
    assert _get(client, "claude-desktop", "https://a.example/cb/").status_code == 400


def test_env_allowlist_ignores_unsafe_entries(client, monkeypatch):
    monkeypatch.setenv(
        "OAUTH_LEGACY_REDIRECT_ALLOWLIST",
        "http://evil.example/cb,javascript:alert(1),ftp://x/y,https://good.example/cb",
    )
    assert _get(client, "claude-desktop", "http://evil.example/cb").status_code == 400
    assert _get(client, "claude-desktop", "https://good.example/cb").status_code == 200


def test_env_allowlist_allows_localhost_http(client, monkeypatch):
    monkeypatch.setenv("OAUTH_LEGACY_REDIRECT_ALLOWLIST", "http://localhost:6274/oauth/callback")
    assert _get(client, "claude-desktop", "http://localhost:6274/oauth/callback").status_code == 200


def test_empty_env_allowlist_rejects_every_legacy_client(client, monkeypatch):
    monkeypatch.setenv("OAUTH_LEGACY_REDIRECT_ALLOWLIST", "   ")
    assert _get(client, "claude-desktop", LEGACY_OK).status_code == 400


# ── input handling / injection ────────────────────────────────────────
@pytest.mark.parametrize("cid", [
    pytest.param('{"$ne": null}', id="ne-operator"),
    pytest.param('{"$gt": ""}', id="gt-operator"),
    pytest.param("a" * 5000, id="5000-chars"),
    pytest.param("\u0000", id="nul-byte"),
    pytest.param("", id="empty"),
])
def test_hostile_client_id_never_opens_the_gate(client, cid):
    assert _get(client, cid, EVIL).status_code == 400


@pytest.mark.asyncio
async def test_get_dcr_client_refuses_non_string_ids(db):
    _register(db, ["https://app.example.org/cb"])
    assert await oauth._get_dcr_client({"$ne": None}) is None
    assert await oauth._get_dcr_client(None) is None
    assert await oauth._get_dcr_client(["dcr-client-1"]) is None


def test_error_body_never_echoes_the_redirect_uri(client):
    probe = "https://evil.example/cb?<script>alert(1)</script>"
    r = _get(client, "claude-desktop", probe)
    assert r.status_code == 400
    assert "<script>" not in r.text and "evil.example" not in r.text


# ── (d) registration-time parser ──────────────────────────────────────
@pytest.mark.parametrize("uri,ok", [
    ("https://example.com/cb", True),
    ("http://localhost:9999/callback", True),
    ("http://127.0.0.1:8080/cb", True),
    ("http://localhost/cb", True),
    ("http://localhost:1@evil.example/", False),      # userinfo trick
    ("http://127.0.0.1:1@evil.example/", False),
    ("https://user:pw@example.com/cb", False),
    ("https://example.com/cb#frag", False),
    ("http://evil.example/cb", False),
    ("ftp://example.com/cb", False),
    ("javascript:alert(1)", False),
    ("https://", False),
    ("https:///cb", False),
    ("https://exa mple.com/cb", False),
    ("https://example.com/cb\n", False),
    (None, False),
    (123, False),
    ("", False),
])
def test_valid_redirect_uri_parser(uri, ok):
    assert oauth._valid_redirect_uri(uri) is ok


def test_register_rejects_userinfo_localhost_bypass(client):
    r = client.post(f"{BASE}/oauth/register", json={"redirect_uris": ["http://localhost:1@evil.example/"]})
    assert r.status_code == 400


# ── token endpoint: replay + binding stay intact ──────────────────────
def _exchange(client, code, verifier, uri):
    return client.post(
        f"{BASE}/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
              "redirect_uri": uri, "client_id": "claude-desktop"},
    )


def test_code_replay_and_redirect_mismatch_are_rejected(client, db):
    verifier = secrets.token_urlsafe(48)
    chal = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = client.post(
        f"{BASE}/oauth/authorize",
        params=_params("claude-desktop", LEGACY_OK, code_challenge=chal),
        data={"email": "victim@example.com", "password": PASSWORD},
    )
    assert r.status_code == 302
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)["code"][0]

    assert _exchange(client, code, verifier, EVIL).status_code == 400          # wrong redirect_uri
    ok = _exchange(client, code, verifier, LEGACY_OK)
    assert ok.status_code == 200 and ok.json()["access_token"].startswith("sk-aurem-oauth-")
    assert _exchange(client, code, verifier, LEGACY_OK).status_code == 400     # replay


def test_wrong_password_never_issues_a_code(client, db):
    r = _post(client, "claude-desktop", LEGACY_OK, password="nope")
    assert r.status_code == 302 and "error=invalid_credentials" in r.headers["location"]
    assert db.oauth_codes.rows == []
    assert "code=" not in r.headers["location"]


def test_expired_code_is_rejected(client, db):
    verifier = secrets.token_urlsafe(48)
    chal = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db.oauth_codes.rows.append({
        "code": "old", "user_id": "u1", "client_id": "claude-desktop", "redirect_uri": LEGACY_OK,
        "scope": "mcp", "resource": "", "code_challenge": chal, "code_challenge_method": "S256",
        "expires_at": datetime.now(UTC) - timedelta(seconds=5), "used": False,
    })
    assert _exchange(client, "old", verifier, LEGACY_OK).status_code == 400
