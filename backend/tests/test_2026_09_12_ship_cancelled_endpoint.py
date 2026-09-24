"""
Backend test for POST /api/aurem-dev/chat/turn/ship-cancelled
(Bug 2 fix — critical-diff cancel bug).

Verifies:
  - Auth required (401 without token)
  - Requires valid session_id (404)
  - Sets turns.<idx>.ship_cancelled=true and persists it
  - Out-of-range turn_index falls back to latest un-shipped assistant turn
  - Negative turn_index rejected with 400
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def seeded_session(auth_headers):
    """Seed a chat session with assistant turns directly via Mongo (no LLM call)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio

    session_id = f"TEST_ship_cancelled_{uuid.uuid4().hex[:8]}"

    async def _seed():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await db.chat_sessions.insert_one({
            "session_id": session_id,
            "user_id": "test_admin_001",
            "created_at": time.time(),
            "turns": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "```aurem-handoff\nfoo\n```"},
                {"role": "user", "content": "STOP cancel"},
                {"role": "assistant", "content": "another handoff"},
            ],
        })
        return session_id

    async def _cleanup():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await db.chat_sessions.delete_one({"session_id": session_id})

    loop = asyncio.new_event_loop()
    try:
        sid = loop.run_until_complete(_seed())
        yield sid
    finally:
        loop.run_until_complete(_cleanup())
        loop.close()


def _fetch_turns(session_id):
    """Fetch turns from Mongo for assertions."""
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio

    async def _f():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        doc = await db.chat_sessions.find_one({"session_id": session_id}, {"_id": 0, "turns": 1})
        return (doc or {}).get("turns", [])

    return asyncio.new_event_loop().run_until_complete(_f())


def test_ship_cancelled_requires_auth():
    r = requests.post(f"{API}/chat/turn/ship-cancelled",
                      json={"session_id": "x", "turn_index": 0}, timeout=30)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}: {r.text}"


def test_ship_cancelled_rejects_negative_index(auth_headers, seeded_session):
    r = requests.post(f"{API}/chat/turn/ship-cancelled", headers=auth_headers,
                      json={"session_id": seeded_session, "turn_index": -1}, timeout=10)
    assert r.status_code == 400, r.text


def test_ship_cancelled_404_on_unknown_session(auth_headers):
    r = requests.post(f"{API}/chat/turn/ship-cancelled", headers=auth_headers,
                      json={"session_id": "nonexistent_TEST_zzz", "turn_index": 0}, timeout=10)
    assert r.status_code == 404, r.text


def test_ship_cancelled_persists_flag(auth_headers, seeded_session):
    # Cancel turn index 1 (first assistant)
    r = requests.post(f"{API}/chat/turn/ship-cancelled", headers=auth_headers,
                      json={"session_id": seeded_session, "turn_index": 1}, timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ok") is True
    assert data.get("turn_index") == 1

    turns = _fetch_turns(seeded_session)
    assert turns[1].get("ship_cancelled") is True
    # Untouched turns should not have the flag
    assert not turns[0].get("ship_cancelled")


def test_ship_cancelled_out_of_range_falls_back_to_latest_unshipped(auth_headers, seeded_session):
    # Index 99 is out of range → should fall back to latest un-shipped assistant (index 3)
    r = requests.post(f"{API}/chat/turn/ship-cancelled", headers=auth_headers,
                      json={"session_id": seeded_session, "turn_index": 99}, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json().get("turn_index") == 3
    turns = _fetch_turns(seeded_session)
    assert turns[3].get("ship_cancelled") is True
