"""test_regression_iter2026_71_single_use_token_race.py — 2026-11-12

Security audit finding (P0): three single-use tokens — password-reset
token, magic-login token, and the OAuth authorization code — were each
consumed with a non-atomic "find, check `used`, THEN separately update
`used=True`" pattern. Two concurrent requests presenting the exact same
still-valid token/code could both pass the read-only check before
either request's write landed, so both could complete successfully —
a genuine single-use violation (two password resets from one link, two
sessions from one magic link, two access tokens from one OAuth code).

Fixed in routers/auth.py (`reset_password`, `_consume_magic_token`,
`magic_login_refresh`) and routers/oauth.py (`oauth_token`) by making
the claim itself the atomic `find_one_and_update` — the same pattern
already proven correct in `routers/github_app.py`'s OAuth-state claim.

These tests fire real CONCURRENT HTTP requests (via a thread pool) at
the live running backend — a plain sequential "use it twice" test
(already covered by test_password_reset_flow.py / test_magic_login_
2026_08_20.py / test_iter182_oauth_pkce.py) cannot catch a check-then-
act race; only genuinely simultaneous requests racing on the real
MongoDB can.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
import requests
from pymongo import MongoClient

BACKEND = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BACKEND}/api/aurem-dev"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


# ── 1. Password reset — concurrent reset with the same token ──────────

@pytest.fixture()
def pwreset_user(db):
    uid = f"TEST_pwrace_{uuid.uuid4().hex[:8]}"
    email = f"TEST_{uid}@aurem.dev"
    hashed = bcrypt.hashpw(b"OriginalRace2026!", bcrypt.gensalt()).decode()
    db.dev_users.insert_one({
        "user_id": uid, "email": email, "password": hashed,
        "name": "PW Race Fixture", "tier": "free", "created_at": time.time(),
    })
    yield {"user_id": uid, "email": email}
    db.dev_users.delete_many({"user_id": uid})
    db.password_reset_tokens.delete_many({"user_id": uid})


def _mint_reset_token(db, user):
    token = secrets.token_urlsafe(32)
    db.password_reset_tokens.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"token": token, "email": user["email"],
                  "expires_at": time.time() + 3600, "used": False,
                  "created_at": time.time()}},
        upsert=True,
    )
    return token


def test_concurrent_reset_with_same_token_only_one_wins(db, pwreset_user):
    token = _mint_reset_token(db, pwreset_user)

    def _reset(new_pw):
        return requests.post(f"{API}/auth/reset-password",
                              json={"token": token, "new_password": new_pw}, timeout=15)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_reset, "RaceWinnerCandidateA2026!")
        f2 = pool.submit(_reset, "RaceWinnerCandidateB2026!")
        r1, r2 = f1.result(), f2.result()

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 400], f"expected exactly one 200 and one 400, got {codes} ({r1.text!r}, {r2.text!r})"

    # Exactly one of the two candidate passwords is now valid — never
    # both (that would mean the token was honored twice), never
    # neither (that would mean the winner's write was lost).
    login_a = requests.post(f"{API}/auth/login",
                             json={"email": pwreset_user["email"], "password": "RaceWinnerCandidateA2026!"}, timeout=15)
    login_b = requests.post(f"{API}/auth/login",
                             json={"email": pwreset_user["email"], "password": "RaceWinnerCandidateB2026!"}, timeout=15)
    assert sorted([login_a.status_code, login_b.status_code]) == [200, 401]

    # Token itself is burned exactly once, not reusable afterward.
    row = db.password_reset_tokens.find_one({"token": token})
    assert row["used"] is True


# ── 2. Magic-login exchange — concurrent exchange, same token ─────────

@pytest.fixture()
def magic_user(db):
    uid = f"TEST_magicrace_{uuid.uuid4().hex[:8]}"
    email = f"TEST_{uid}@aurem.dev"
    db.dev_users.insert_one({
        "user_id": uid, "email": email, "name": "Magic Race Fixture",
        "tier": "free", "tokens_remaining": 10000, "is_admin": False,
        "password": None, "created_at": datetime.now(timezone.utc),
    })
    yield {"user_id": uid, "email": email}
    db.dev_users.delete_many({"user_id": uid})
    db.magic_login_tokens.delete_many({"user_id": uid})


def _mint_magic_token(db, user, *, expired=False):
    tok = secrets.token_urlsafe(32)
    now = time.time()
    db.magic_login_tokens.insert_one({
        "token": tok, "user_id": user["user_id"], "email": user["email"],
        "stage": "stage1_github_started",
        "created_at": now - 8 * 86400 if expired else now,
        "expires_at": now - 86400 if expired else now + 7 * 86400,
        "used": False,
    })
    return tok


def test_concurrent_magic_exchange_same_token_only_one_wins(db, magic_user):
    tok = _mint_magic_token(db, magic_user)

    def _exchange():
        return requests.post(f"{API}/auth/magic-login/exchange", json={"token": tok}, timeout=15)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_exchange)
        f2 = pool.submit(_exchange)
        r1, r2 = f1.result(), f2.result()

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 410], f"expected exactly one 200 and one 410, got {codes} ({r1.text!r}, {r2.text!r})"
    row = db.magic_login_tokens.find_one({"token": tok})
    assert row["used"] is True


def test_concurrent_magic_refresh_same_old_token_only_one_wins(db, magic_user):
    old_tok = _mint_magic_token(db, magic_user, expired=True)

    def _refresh():
        return requests.post(f"{API}/auth/magic-login/refresh", json={"token": old_tok}, timeout=15)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_refresh)
        f2 = pool.submit(_refresh)
        r1, r2 = f1.result(), f2.result()

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 410], f"expected exactly one 200 and one 410, got {codes} ({r1.text!r}, {r2.text!r})"

    # Exactly ONE replacement token should have been minted for the
    # old one — a double-win would have minted two live sessions from
    # a single expired link.
    replacements = list(db.magic_login_tokens.find(
        {"user_id": magic_user["user_id"], "token": {"$ne": old_tok}}))
    assert len(replacements) == 1, f"expected exactly 1 replacement token, found {len(replacements)}"


# ── 3. OAuth code exchange — concurrent token request, same code ──────

def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _make_oauth_user():
    email = f"oauth-race-{int(time.time()*1000)}-{secrets.token_hex(3)}@aurem.dev"
    pw = "OAuthRacePass2026!"
    r = requests.post(f"{API}/auth/signup", json={"email": email, "password": pw, "name": "OAuth Race"}, timeout=15)
    assert r.status_code == 200, r.text
    return email, pw


def _issue_oauth_code(email, pw, challenge):
    qs = {
        "response_type": "code", "client_id": "claude-desktop",
        "redirect_uri": "https://claude.ai/cb", "scope": "mcp",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    r = requests.post(f"{API}/oauth/authorize", params=qs,
                       data={"email": email, "password": pw}, timeout=15, allow_redirects=False)
    assert r.status_code == 302, r.text
    import urllib.parse as up
    loc = r.headers["location"]
    q = up.parse_qs(up.urlparse(loc).query)
    return q["code"][0]


def test_concurrent_oauth_token_exchange_same_code_only_one_wins(db):
    email, pw = _make_oauth_user()
    verifier, challenge = _pkce_pair()
    code = _issue_oauth_code(email, pw, challenge)

    def _exchange():
        return requests.post(f"{API}/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": "https://claude.ai/cb", "client_id": "claude-desktop",
            "code_verifier": verifier,
        }, timeout=15)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_exchange)
        f2 = pool.submit(_exchange)
        r1, r2 = f1.result(), f2.result()

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 400], f"expected exactly one 200 and one 400, got {codes} ({r1.text!r}, {r2.text!r})"
    winner = r1 if r1.status_code == 200 else r2
    token = winner.json()["access_token"]
    # Exactly one api_keys row for this exact token — not minted twice.
    assert db.api_keys.count_documents({"key": token}) == 1


def test_wrong_pkce_verifier_does_not_burn_a_legitimate_code(db):
    """A failed PKCE attempt must never consume the code out from
    under the real client's correct retry — PKCE validation happens
    BEFORE the atomic burn."""
    email, pw = _make_oauth_user()
    verifier, challenge = _pkce_pair()
    code = _issue_oauth_code(email, pw, challenge)

    bad = requests.post(f"{API}/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "https://claude.ai/cb", "client_id": "claude-desktop",
        "code_verifier": secrets.token_urlsafe(64),  # wrong verifier
    }, timeout=15)
    assert bad.status_code == 400

    # The SAME code with the CORRECT verifier must still work — the
    # bad attempt above must not have burned it.
    good = requests.post(f"{API}/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "https://claude.ai/cb", "client_id": "claude-desktop",
        "code_verifier": verifier,
    }, timeout=15)
    assert good.status_code == 200, good.text
    assert good.json()["access_token"].startswith("sk-aurem-oauth-")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
