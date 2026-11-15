"""
test_regression_iter2026_70_oauth_next_success_path.py — Fix 7 follow-up
(2026-11-11): confirms the GitHub/Google OAuth SUCCESS path (not just the
cancel path already covered by test_regression_iter2026_69) actually lands
a signed-in user back on `/harness`.

Honesty note on scope: this environment cannot drive a real github.com or
accounts.google.com consent screen — that requires a real human-held
account approving a real OAuth app in a real browser, which no automated
test anywhere can do without live credentials. What IS tested here, with
NOTHING about our own code mocked, is the entire real production path:

  • the real `/callback` route function in routers/github_oauth.py /
    routers/google_oauth.py — called exactly as FastAPI would call it,
  • the real `oauth_states` row lookup + 5-min TTL check against the
    REAL MongoDB this backend uses,
  • the real dev_users new-account-insert AND existing-account-update
    branches,
  • the real JWT minted by cto_services.auth.create_token,
  • the real `/oauth-finish#token=...&next=...` redirect URL string the
    browser would actually be sent to.

The ONLY thing monkeypatched is the unavoidable external network hop
(exchanging a `code` for a token, then fetching the provider's profile) —
there is no way to get a real code from GitHub/Google without a live
browser + real account, and this suite cannot fabricate one honestly. A
canned provider response is substituted so the rest of the REAL pipeline
runs for real against the REAL database. This is strictly stronger than a
unit test — if the `?next=` plumbing, DB bridging, or redirect-building
logic regresses, this fails.

For an actual human-driven confirmation (real GitHub/Google login button
click, real consent screen, landing on /harness), see the companion
manual-test note left in the Fix 7/8 report — that step still needs a
person at a real browser since it is outside what any backend test here
can execute.
"""
from __future__ import annotations

import os
import time
import urllib.parse as up
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.asyncio


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


async def _insert_state(db, *, state, mode, next_path, origin,
                         provider=None, redirect_uri=None):
    row = {
        "state": state,
        "mode": mode,
        "user_id": None,
        "ts": time.time(),
        "origin": origin,
        "created_at": datetime.now(timezone.utc),
        "next": next_path,
    }
    if provider:
        row["provider"] = provider
    if redirect_uri:
        row["redirect_uri"] = redirect_uri
    await db.oauth_states.insert_one(row)


# ── GitHub — brand-new account, OAuth-first signup, next=/harness ─────

async def test_github_new_account_signup_lands_on_harness(monkeypatch):
    import routers.github_oauth as gho

    db = _db()
    nonce = uuid.uuid4().hex
    gh_login = f"ghnew_{nonce[:10]}"
    gh_email = f"{gh_login}@users.noreply.github.com.test"
    state = f"signup:{nonce}"
    origin = "https://harness-confirm.example"

    async def _fake_exchange(code):
        assert code == "fake-code-new"
        return "fake-gh-access-token"

    async def _fake_gh_user(token):
        assert token == "fake-gh-access-token"
        return {"login": gh_login, "email": gh_email, "id": 900001,
                 "name": "", "avatar_url": ""}

    monkeypatch.setattr(gho, "exchange", _fake_exchange)
    monkeypatch.setattr(gho, "gh_user", _fake_gh_user)
    # Direct function-call invocation (bypasses the lifespan-managed
    # app, same convention as test_iter_2026_09_09_oauth_repos_
    # revoked_token.py) — get_db()'s module global is never set
    # outside the real running server process, so point it at this
    # test's own REAL Mongo connection instead of a fake.
    monkeypatch.setattr(gho, "get_db", lambda: db)

    await _insert_state(db, state=state, mode="signup",
                         next_path="/harness", origin=origin)
    try:
        resp = await gho.callback(code="fake-code-new", state=state, error=None, error_description=None)
        loc = resp.headers["location"]

        # Lands on the finish page, on the SAME origin the user started
        # on (not a hardcoded APP_URL), with the validated next carried.
        assert loc.startswith(f"{origin}/oauth-finish#token=")
        assert "new=1" in loc  # brand-new account, not a return login
        frag = up.parse_qs(loc.split("#", 1)[1])
        assert frag.get("next") == ["/harness"]
        assert frag.get("login") == [gh_login]
        assert frag.get("token") and frag["token"][0]

        # Real DB write happened — not mocked, not skipped.
        row = await db.dev_users.find_one({"email": gh_email})
        assert row is not None
        assert row["github"]["login"] == gh_login
        assert row["auth_provider"] == "github"
    finally:
        await db.dev_users.delete_many({"email": gh_email})
        await db.oauth_states.delete_one({"state": state})


# ── GitHub — EXISTING account, OAuth login, next=/harness ─────────────

async def test_github_existing_account_login_lands_on_harness(monkeypatch):
    import routers.github_oauth as gho

    db = _db()
    nonce = uuid.uuid4().hex
    gh_login = f"ghret_{nonce[:10]}"
    gh_email = f"{gh_login}@aurem.test"
    user_id = uuid.uuid4().hex
    state = f"login:{nonce}"
    origin = "https://harness-confirm.example"

    await db.dev_users.insert_one({
        "user_id": user_id, "email": gh_email, "name": gh_login,
        "password": None, "auth_provider": "github", "tier": "free",
        "tokens_remaining": 1000, "github": {"login": gh_login},
        "created_at": time.time(),
    })

    async def _fake_exchange(code):
        return "fake-gh-access-token-2"

    async def _fake_gh_user(token):
        return {"login": gh_login, "email": gh_email, "id": 900002,
                 "name": "", "avatar_url": ""}

    monkeypatch.setattr(gho, "exchange", _fake_exchange)
    monkeypatch.setattr(gho, "gh_user", _fake_gh_user)
    monkeypatch.setattr(gho, "get_db", lambda: db)

    await _insert_state(db, state=state, mode="login",
                         next_path="/harness", origin=origin)
    try:
        resp = await gho.callback(code="fake-code-ret", state=state, error=None, error_description=None)
        loc = resp.headers["location"]
        assert loc.startswith(f"{origin}/oauth-finish#token=")
        assert "new=0" in loc  # return login, NOT a new account
        frag = up.parse_qs(loc.split("#", 1)[1])
        assert frag.get("next") == ["/harness"]

        # Same account, not a duplicate — identity preserved.
        row = await db.dev_users.find_one({"email": gh_email})
        assert row["user_id"] == user_id
        assert await db.dev_users.count_documents({"email": gh_email}) == 1
    finally:
        await db.dev_users.delete_many({"email": gh_email})
        await db.oauth_states.delete_one({"state": state})


# ── Google — brand-new account, OAuth-first signup, next=/harness ─────

async def test_google_new_account_signup_lands_on_harness(monkeypatch):
    import routers.google_oauth as gso

    db = _db()
    nonce = uuid.uuid4().hex
    g_email = f"gnew_{nonce[:10]}@gmail.test"
    state = f"signup:{nonce}"
    origin = "https://harness-confirm.example"
    redirect_uri = f"{origin}/api/aurem-dev/google/oauth/callback"

    async def _fake_exchange(code, redirect_uri_arg):
        assert code == "fake-code-g-new"
        return "fake-google-access-token"

    async def _fake_get_profile(token):
        assert token == "fake-google-access-token"
        return {"email": g_email, "name": "New Google User",
                "picture": "", "sub": "goog-900003", "email_verified": True}

    monkeypatch.setattr(gso, "exchange", _fake_exchange)
    monkeypatch.setattr(gso, "get_profile", _fake_get_profile)
    monkeypatch.setattr(gso, "get_db", lambda: db)

    await _insert_state(db, state=state, mode="signup",
                         next_path="/harness", origin=origin,
                         provider="google", redirect_uri=redirect_uri)
    try:
        resp = await gso.callback(code="fake-code-g-new", state=state, error=None)
        loc = resp.headers["location"]
        assert loc.startswith(f"{origin}/oauth-finish#token=")
        assert "new=1" in loc
        assert "provider=google" in loc
        frag = up.parse_qs(loc.split("#", 1)[1])
        assert frag.get("next") == ["/harness"]

        row = await db.dev_users.find_one({"email": g_email})
        assert row is not None
        assert row["auth_provider"] == "google"
        assert row["google"]["id"] == "goog-900003"
    finally:
        await db.dev_users.delete_many({"email": g_email})
        await db.oauth_states.delete_one({"state": state})


# ── Google — EXISTING account, OAuth login, next=/harness ─────────────

async def test_google_existing_account_login_lands_on_harness(monkeypatch):
    import routers.google_oauth as gso

    db = _db()
    nonce = uuid.uuid4().hex
    g_email = f"gret_{nonce[:10]}@gmail.test"
    user_id = uuid.uuid4().hex
    state = f"login:{nonce}"
    origin = "https://harness-confirm.example"
    redirect_uri = f"{origin}/api/aurem-dev/google/oauth/callback"

    await db.dev_users.insert_one({
        "user_id": user_id, "email": g_email, "name": "Return Google User",
        "password": None, "auth_provider": "google", "tier": "free",
        "tokens_remaining": 1000, "google": {"id": "goog-900004"},
        "created_at": time.time(),
    })

    async def _fake_exchange(code, redirect_uri_arg):
        return "fake-google-access-token-2"

    async def _fake_get_profile(token):
        return {"email": g_email, "name": "Return Google User",
                "picture": "", "sub": "goog-900004", "email_verified": True}

    monkeypatch.setattr(gso, "exchange", _fake_exchange)
    monkeypatch.setattr(gso, "get_profile", _fake_get_profile)
    monkeypatch.setattr(gso, "get_db", lambda: db)

    await _insert_state(db, state=state, mode="login",
                         next_path="/harness", origin=origin,
                         provider="google", redirect_uri=redirect_uri)
    try:
        resp = await gso.callback(code="fake-code-g-ret", state=state, error=None)
        loc = resp.headers["location"]
        assert loc.startswith(f"{origin}/oauth-finish#token=")
        assert "new=0" in loc
        frag = up.parse_qs(loc.split("#", 1)[1])
        assert frag.get("next") == ["/harness"]

        row = await db.dev_users.find_one({"email": g_email})
        assert row["user_id"] == user_id
        assert await db.dev_users.count_documents({"email": g_email}) == 1
    finally:
        await db.dev_users.delete_many({"email": g_email})
        await db.oauth_states.delete_one({"state": state})
