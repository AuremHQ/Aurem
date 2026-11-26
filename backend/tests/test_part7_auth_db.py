"""
test_part7_auth_db.py — Part VII audit fixes 1(a)–1(d).

Offline unit tests (fake Mongo surface only — no live backend, no live
Mongo, no PIN). Run from backend/ with the agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part7_auth_db.py -q

  1(a) JWT_SECRET fail-fast guard in cto_services/auth.py itself.
  1(b) Fail-CLOSED revocation with a short-TTL cache
       (services/token_revocation.py + the gates in auth.py).
  1(c) Age-based eviction of _last_presence_write.
  1(d) _loop_dbs keyed on the loop object + client closed on loop close.

NOTE on 1(b): this reverses a DOCUMENTED deliberate trade-off (Iter 307
fail-open, citing Amazon/GitHub). These tests pin the NEW behaviour:
a DB error with no fresh cached answer DENIES. See FIX-REPORT.md.
"""
from __future__ import annotations

import asyncio
import gc
import os
import subprocess
import sys
import time
from pathlib import Path

import jwt
import pytest
from fastapi import HTTPException

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 1(a) — auth.py raises at import time without JWT_SECRET, so the secret
# must exist BEFORE the import below. (The unset case is covered by a
# subprocess test that never imports it in-process.)
os.environ.setdefault("JWT_SECRET", "part7-test-secret-32-chars-minimum!")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "aurem_dev_test")

import cto_services.auth as auth
import cto_services.db as dbmod
from services import token_revocation as rev


# ── Fake Mongo surface ──────────────────────────────────────────────

class _FakeColl:
    def __init__(self, find_one_result=None, find_one_exc=None):
        self._result = find_one_result
        self._exc = find_one_exc
        self.update_calls: list = []

    async def find_one(self, filt, proj=None):
        if self._exc is not None:
            raise self._exc
        return self._result

    async def update_one(self, filt, update, upsert=False):
        self.update_calls.append((filt, update, upsert))

        class _R:
            modified_count = 1

        return _R()

    async def create_index(self, *a, **k):
        return "idx"


class _FakeDB:
    """Supports both db['revoked_tokens'] and db.dev_users access."""

    def __init__(self, revoked=None, dev_row=None, exc=None):
        self._revoked = _FakeColl(find_one_result=revoked, find_one_exc=exc)
        self._dev = _FakeColl(find_one_result=dev_row, find_one_exc=exc)

    def __getitem__(self, name):
        if name == rev.REVOKED_COLL:
            return self._revoked
        raise KeyError(name)

    @property
    def dev_users(self):
        return self._dev


def _raw_token(payload: dict) -> str:
    now = int(time.time())
    full = {"user_id": "u1", "email": "u1@aurem.test",
            "iat": now, "jti": "jti-part7", "exp": now + 3600}
    full.update(payload)
    return jwt.encode(full, auth.JWT_SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def _clean_state():
    rev.clear_revocation_cache()
    auth._last_presence_write.clear()
    yield
    rev.clear_revocation_cache()
    auth._last_presence_write.clear()


# ── 1(a) JWT_SECRET fail-fast ───────────────────────────────────────

def test_1a_import_without_secret_raises():
    """A process without JWT_SECRET must fail at import, not start up
    and 500 on first auth use."""
    env = dict(os.environ)
    env.pop("JWT_SECRET", None)
    env["PYTHONPATH"] = str(_BACKEND_DIR)
    proc = subprocess.run(
        [sys.executable, "-c", "import cto_services.auth"],
        cwd=str(_BACKEND_DIR), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0
    assert "JWT_SECRET must be set" in proc.stderr


def test_1a_empty_key_raises_invalid_key_error():
    """Documents WHY the guard exists (pinned PyJWT behaviour): an
    empty HMAC key raises InvalidKeyError at first use — NOT an
    InvalidTokenError, so current_dev's handler would not turn it
    into a 401. The guard raises before this path is reachable."""
    with pytest.raises(jwt.InvalidKeyError):
        jwt.encode({"user_id": "u1"}, "", algorithm="HS256")


def test_1a_create_token_roundtrip_with_secret():
    token = auth.create_token("u1", "u1@aurem.test")
    payload = jwt.decode(token, auth.JWT_SECRET, algorithms=["HS256"])
    assert payload["user_id"] == "u1"
    assert payload["jti"] and payload["iat"] and payload["exp"]


# ── 1(b) fail-closed revocation ─────────────────────────────────────

@pytest.mark.asyncio
async def test_1b_jti_check_happy_path():
    assert await rev.is_jti_revoked(_FakeDB(revoked={"_id": 1}), "j") is True
    assert await rev.is_jti_revoked(_FakeDB(revoked=None), "j") is False


@pytest.mark.asyncio
async def test_1b_jti_check_db_error_raises_without_cache():
    """The core reversal: DB error + no cached answer = raise, never
    return False (the old fail-open behaviour)."""
    with pytest.raises(rev.RevocationCheckUnavailable):
        await rev.is_jti_revoked(_FakeDB(exc=RuntimeError("mongo down")), "j")


@pytest.mark.asyncio
async def test_1b_jti_check_db_error_serves_fresh_cache():
    """A transient blip does NOT lock everyone out: a fresh cached
    answer (positive or negative) is served while the DB is down."""
    db_ok = _FakeDB(revoked=None)
    assert await rev.is_jti_revoked(db_ok, "j") is False  # primes cache
    db_down = _FakeDB(exc=RuntimeError("mongo down"))
    assert await rev.is_jti_revoked(db_down, "j") is False  # cached


@pytest.mark.asyncio
async def test_1b_jti_check_stale_cache_denies():
    """Once the 30 s cache drains, a still-down DB denies."""
    db_ok = _FakeDB(revoked=None)
    assert await rev.is_jti_revoked(db_ok, "j") is False
    # Backdate the cached entry past its TTL.
    key = ("jti", "j")
    answer, _exp = rev._revocation_cache[key]
    rev._revocation_cache[key] = (answer, time.monotonic() - 1)
    with pytest.raises(rev.RevocationCheckUnavailable):
        await rev.is_jti_revoked(_FakeDB(exc=RuntimeError("mongo down")), "j")


@pytest.mark.asyncio
async def test_1b_revoke_jti_write_through_cache():
    """Revoking writes through to the cache: the token is dead on this
    worker immediately, even if the DB dies a millisecond later."""
    db = _FakeDB()
    assert await rev.revoke_jti(db, "j", int(time.time()) + 3600) is True
    db_down = _FakeDB(exc=RuntimeError("mongo down"))
    assert await rev.is_jti_revoked(db_down, "j") is True


@pytest.mark.asyncio
async def test_1b_barrier_check_and_invalidation():
    now = int(time.time())
    db = _FakeDB(dev_row={"session_barrier_at": now + 100})
    assert await rev.is_iat_before_barrier(db, "u1", now) is True
    db2 = _FakeDB(dev_row={})
    assert await rev.is_iat_before_barrier(db2, "u1", now) is False
    # Prime a negative, then revoke-all: the cached negative must die.
    assert await rev.is_iat_before_barrier(db2, "u1", now) is False
    assert await rev.revoke_all_for_user(db2, "u1") == 1
    with pytest.raises(rev.RevocationCheckUnavailable):
        await rev.is_iat_before_barrier(
            _FakeDB(exc=RuntimeError("mongo down")), "u1", now)


@pytest.mark.asyncio
async def test_1b_current_dev_denies_when_db_down(monkeypatch):
    """End-to-end gate: a revoked-or-unverifiable token must not sail
    through current_dev during a Mongo outage."""
    token = _raw_token({})
    monkeypatch.setattr(
        "cto_services.db.get_db",
        lambda: _FakeDB(exc=RuntimeError("mongo down")),
    )
    with pytest.raises(HTTPException) as ei:
        await auth.current_dev(f"Bearer {token}")
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_1b_current_dev_allows_on_fresh_cache_during_outage(monkeypatch):
    """Same outage, but this token's negative answer is still fresh in
    the cache — the request survives the blip."""
    token = _raw_token({})
    good = _FakeDB(dev_row=None)
    monkeypatch.setattr("cto_services.db.get_db", lambda: good)
    payload = await auth.current_dev(f"Bearer {token}")
    assert payload["user_id"] == "u1"
    monkeypatch.setattr(
        "cto_services.db.get_db",
        lambda: _FakeDB(exc=RuntimeError("mongo down")),
    )
    payload2 = await auth.current_dev(f"Bearer {token}")
    assert payload2["user_id"] == "u1"
    await asyncio.sleep(0)  # let _touch_presence's background write run


@pytest.mark.asyncio
async def test_1b_current_dev_rejects_revoked_token(monkeypatch):
    token = _raw_token({})
    monkeypatch.setattr(
        "cto_services.db.get_db",
        lambda: _FakeDB(revoked={"_id": 1}, dev_row=None),
    )
    with pytest.raises(HTTPException) as ei:
        await auth.current_dev(f"Bearer {token}")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_1b_link_token_single_use_fail_closed(monkeypatch):
    """A link token whose single-use write cannot be recorded is
    denied, not silently replayable."""
    token = auth.create_link_token("u1", "u1@aurem.test")
    monkeypatch.setattr(
        "cto_services.db.get_db",
        lambda: _FakeDB(exc=RuntimeError("mongo down")),
    )
    with pytest.raises(HTTPException) as ei:
        await auth.consume_link_token(token)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_1b_link_token_consumed_once(monkeypatch):
    token = auth.create_link_token("u1", "u1@aurem.test")
    db = _FakeDB()
    monkeypatch.setattr("cto_services.db.get_db", lambda: db)
    payload = await auth.consume_link_token(token)
    assert payload is not None and payload["user_id"] == "u1"
    assert len(db._revoked.update_calls) == 1
    # Second use: the revocation store now reports the jti → 401.
    db2 = _FakeDB(revoked={"_id": 1})
    monkeypatch.setattr("cto_services.db.get_db", lambda: db2)
    with pytest.raises(HTTPException) as ei:
        await auth.consume_link_token(token)
    assert ei.value.status_code == 401


# ── 1(c) presence eviction ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_1c_old_entries_evicted():
    now = time.monotonic()
    auth._last_presence_write["old-user"] = now - 100.0   # >> 45 s
    auth._last_presence_write["recent-user"] = now          # fresh
    auth._touch_presence("new-user")
    assert "old-user" not in auth._last_presence_write
    assert "recent-user" in auth._last_presence_write
    assert "new-user" in auth._last_presence_write
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_1c_throttle_still_works():
    auth._touch_presence("u1")
    first = auth._last_presence_write["u1"]
    auth._touch_presence("u1")  # within 45 s: no rewrite
    assert auth._last_presence_write["u1"] == first
    assert len(auth._last_presence_write) == 1
    # Backdate past the throttle: the next touch rewrites.
    auth._last_presence_write["u1"] = time.monotonic() - 46.0
    auth._touch_presence("u1")
    assert auth._last_presence_write["u1"] > first
    await asyncio.sleep(0)


# ── 1(d) loop-keyed db cache ────────────────────────────────────────

@pytest.fixture
def _db_state(monkeypatch):
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
    monkeypatch.setenv("DB_NAME", "aurem_dev_test")
    dbmod._db = object()
    # _main_loop is irrelevant here (these tests call
    # _secondary_db_for_current_loop directly, never get_db), so it
    # stays None — which also keeps this fixture sync, so the tests
    # below can drive their own ephemeral loops from a thread with no
    # running loop (run_until_complete forbids nesting inside the
    # pytest-asyncio loop).
    dbmod._main_loop = None
    dbmod._loop_dbs.clear()
    yield
    dbmod._db = None
    dbmod._main_loop = None
    dbmod._loop_dbs.clear()


def _run_in_loop(loop, fn):
    return loop.run_until_complete(fn())


def test_1d_distinct_loops_get_distinct_clients(_db_state):
    """Two loops must never share a client (the old id() key could
    hand a new loop a stale client bound to a dead loop)."""
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _get():
        return dbmod._secondary_db_for_current_loop()

    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    try:
        db_a = _run_in_loop(loop_a, _get)
        db_a2 = _run_in_loop(loop_a, _get)
        db_b = _run_in_loop(loop_b, _get)
        assert db_a is db_a2            # same loop → same cached client
        assert db_a is not db_b         # different loop → different client
        assert isinstance(db_a.client, AsyncIOMotorClient)
        assert db_a.client is not db_b.client
    finally:
        loop_a.close()
        loop_b.close()


def test_1d_closed_loop_client_closed_and_evicted(_db_state, monkeypatch):
    """Sweep path: a closed loop's client is closed and its entry
    evicted on the next lookup from another loop."""
    from motor.motor_asyncio import AsyncIOMotorClient

    closed: list = []
    real_client = AsyncIOMotorClient

    class _SpyClient(real_client):
        def close(self):
            closed.append(self)
            return super().close()

    monkeypatch.setattr(
        "motor.motor_asyncio.AsyncIOMotorClient", _SpyClient)

    async def _get():
        return dbmod._secondary_db_for_current_loop()

    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    try:
        db_a = _run_in_loop(loop_a, _get)
        client_a = db_a.client
        assert client_a not in closed
        loop_a.close()
        db_b = _run_in_loop(loop_b, _get)   # sweep runs here
        assert db_b is not db_a
        assert client_a in closed           # swept + closed
        assert len(dbmod._loop_dbs) == 1    # only loop_b remains
    finally:
        loop_b.close()


def test_1d_gc_loop_closes_client_and_no_id_reuse(_db_state, monkeypatch):
    """finalize path: GC'ing a loop closes its client, and a fresh loop
    afterwards never inherits the dead loop's db handle."""
    from motor.motor_asyncio import AsyncIOMotorClient

    closed: list = []
    real_client = AsyncIOMotorClient

    class _SpyClient(real_client):
        def close(self):
            closed.append(self)
            return super().close()

    monkeypatch.setattr(
        "motor.motor_asyncio.AsyncIOMotorClient", _SpyClient)

    async def _get():
        return dbmod._secondary_db_for_current_loop()

    loop_a = asyncio.new_event_loop()
    db_a = _run_in_loop(loop_a, _get)
    client_a = db_a.client
    loop_a.close()
    del loop_a
    gc.collect()
    assert client_a in closed            # finalize closed it on GC
    loop_b = asyncio.new_event_loop()
    try:
        db_b = _run_in_loop(loop_b, _get)
        assert db_b is not db_a         # no id()-reuse hand-off
    finally:
        loop_b.close()
