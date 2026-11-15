"""
test_regression_iter2026_69_oauth_next_redirect.py — Fix 7 (2026-11-11):
GitHub/Google signup+login OAuth must honor a safe `?next=` return path
(e.g. /harness) instead of always landing on /dashboard.

Bug: the "Continue with GitHub"/"Continue with Google" buttons on
/login and /signup never carried the page's own `?next=` forward into
the OAuth round-trip — a signup/login started from /harness always
landed on /dashboard, never back on /harness.

Fix: `/connect` and `/start` now accept `?next=`, validate it
(_safe_next — same-origin relative path only: starts with "/", not
"//", no backslashes), store it on the `oauth_states` row, and the
callback (success AND cancel paths) carries it through to the
frontend redirect.

Hits the live backend directly (same pattern as test_iter82_oauth_
signup.py) — no mocking, real Mongo.
"""
from __future__ import annotations

import os
import urllib.parse as up

import httpx
import pytest

from routers.github_oauth import _safe_next

API = "http://localhost:8001/api/aurem-dev"


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


# ── 1. _safe_next unit tests ──────────────────────────────────────────

def test_safe_next_accepts_plain_relative_path():
    assert _safe_next("/harness") == "/harness"


def test_safe_next_accepts_path_with_query():
    assert _safe_next("/projects/42?tab=activity") == "/projects/42?tab=activity"


@pytest.mark.parametrize("bad", [
    None, "", "https://evil.com", "http://evil.com", "//evil.com",
    "/\\evil.com", "\\\\evil.com", "javascript:alert(1)", "a" * 3000,
])
def test_safe_next_rejects_everything_else(bad):
    assert _safe_next(bad) is None


# ── 2. GitHub /connect?signup=1&next=... persists the validated next ──

@pytest.mark.asyncio
async def test_github_connect_persists_safe_next():
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
        r = await c.get(f"{API}/github/oauth/connect?signup=1&intent=login&next=%2Fharness")
    loc = r.headers.get("location") or ""
    qs = up.parse_qs(up.urlparse(loc).query)
    state = qs.get("state", [""])[0]
    assert state, f"no state in redirect: {loc}"
    db = _db()
    try:
        row = await db.oauth_states.find_one({"state": state})
        assert row is not None
        assert row.get("next") == "/harness"
    finally:
        await db.oauth_states.delete_one({"state": state})


@pytest.mark.asyncio
async def test_github_connect_malicious_next_stored_as_none():
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
        r = await c.get(f"{API}/github/oauth/connect?signup=1&next=https%3A%2F%2Fevil.com")
    loc = r.headers.get("location") or ""
    state = up.parse_qs(up.urlparse(loc).query).get("state", [""])[0]
    db = _db()
    try:
        row = await db.oauth_states.find_one({"state": state})
        assert row.get("next") is None
    finally:
        await db.oauth_states.delete_one({"state": state})


# ── 3. Google /start?next=... persists the validated next ─────────────

@pytest.mark.asyncio
async def test_google_start_persists_safe_next():
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
        r = await c.get(f"{API}/google/oauth/start?intent=login&next=%2Fharness")
    loc = r.headers.get("location") or ""
    state = up.parse_qs(up.urlparse(loc).query).get("state", [""])[0]
    assert state, f"no state in redirect: {loc}"
    db = _db()
    try:
        row = await db.oauth_states.find_one({"state": state})
        assert row is not None
        assert row.get("provider") == "google"
        assert row.get("next") == "/harness"
    finally:
        await db.oauth_states.delete_one({"state": state})


@pytest.mark.asyncio
async def test_google_start_malicious_next_stored_as_none():
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
        r = await c.get(f"{API}/google/oauth/start?next=%2F%2Fevil.com")
    loc = r.headers.get("location") or ""
    state = up.parse_qs(up.urlparse(loc).query).get("state", [""])[0]
    db = _db()
    try:
        row = await db.oauth_states.find_one({"state": state})
        assert row.get("next") is None
    finally:
        await db.oauth_states.delete_one({"state": state})


# ── 4. Cancelled attempt still carries `next` back to the retry page ──

@pytest.mark.asyncio
async def test_github_cancel_carries_next_back_to_login():
    """Simulates the user clicking Cancel on GitHub's consent screen
    after starting from /harness — the retry-landing page (/login)
    must still have ?next=/harness so a second attempt isn't stranded."""
    db = _db()
    state = "login:regr69-cancel-test"
    await db.oauth_states.insert_one({
        "state": state, "mode": "login", "origin": "https://example.test",
        "next": "/harness",
    })
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
            r = await c.get(
                f"{API}/github/oauth/callback?error=access_denied&state={state}"
            )
        loc = r.headers.get("location") or ""
        assert "/login" in loc
        assert "github=cancelled" in loc
        assert "next=%2Fharness" in loc or "next=/harness" in loc
    finally:
        await db.oauth_states.delete_one({"state": state})


@pytest.mark.asyncio
async def test_google_cancel_carries_next_back_to_login():
    db = _db()
    state = "login:regr69-google-cancel-test"
    await db.oauth_states.insert_one({
        "state": state, "mode": "login", "origin": "https://example.test",
        "next": "/harness",
    })
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
            r = await c.get(
                f"{API}/google/oauth/callback?error=access_denied&state={state}"
            )
        loc = r.headers.get("location") or ""
        assert "/login" in loc
        assert "google=cancelled" in loc
        assert "next=%2Fharness" in loc or "next=/harness" in loc
    finally:
        await db.oauth_states.delete_one({"state": state})
