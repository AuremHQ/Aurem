"""
test_p1j_context_bridge.py — Part VII audit fixes 4(b) server half + 4(d)
(backend side: the bridge endpoint's token gate).

Offline unit tests (fake Mongo surface only — no live backend, no live
Mongo). Run from backend/ with the agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_p1j_context_bridge.py -q

  4(b) server half:
    - `create_context_bridge_token` mints a 15-minute token with
      `scope: "context_bridge"`, bound to the parent session
      (`session_jti` / `session_iat`), preserving the `mfa` claim.
    - `require_context_bridge_token` accepts ONLY such a token: a full
      7-day session JWT, an expired bridge token, a wrong-scope token,
      an MFA-pending token, a bridge token whose parent session was
      revoked / barriered are all 401; a DB failure while checking the
      parent session is 503 (fail-closed).
    - POST /auth/context-bridge-token (admin-only) mints one from a
      valid session JWT and refuses bridge tokens, MFA-pending tokens
      and pre-jti session tokens as input.
    - POST /admin/ora/context-bridge (the handler) accepts a bridge
      token and rejects a session JWT.

NOTE: the mint endpoint and the bridge handler are exercised through
the real `require_admin` / `current_dev` code paths, with only the
DB surface and the `services.*` helpers faked. `services.usage`,
`services.mfa`, `services.rate_limiter`, `services.chat_helpers`
and `services.admin_team` are stubbed in `sys.modules` because the
real modules are outside this lane's seeded tree; the stubs only
answer "is this the founder?" / record calls — they do not re-implement
any auth decision.
"""
from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault("JWT_SECRET", "p1j-test-secret-32-chars-minimum!")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "aurem_dev_test")

FOUNDER_EMAIL = "founder@example.com"
USER_ID = "user-bridge-1"


# ── Stub `services.*` helpers (not in this lane's seeded tree) ─────────
def _delegating_fake(name, **attrs):
    """Build a fake module that returns `attrs` for the names it
    defines and delegates any OTHER attribute to the REAL module
    (same fix as tests/test_part15_admin_fixes.py). The old bare
    fake broke `from <name> import <other>` for every other test
    file collected after this one (e.g. this file's fake
    services.usage lacked get_usage; its fake services.chat_helpers
    lacked enforce_turn_completion_signal). Defined attributes win,
    so this file's own tests still see the recording fakes; anything
    else falls through to the real module.
    """
    import importlib

    fake = types.ModuleType(name)
    for _k, _v in attrs.items():
        setattr(fake, _k, _v)
    _real = [None]

    def __getattr__(attr):
        if _real[0] is None:
            saved = sys.modules.pop(name, None)
            try:
                _real[0] = importlib.import_module(name)
            finally:
                if saved is not None:
                    sys.modules[name] = saved
        return getattr(_real[0], attr)

    fake.__getattr__ = __getattr__
    return fake


def _install_service_stubs() -> dict:
    calls: dict = {"persist_turn": [], "founder_checks": []}
    pkg = types.ModuleType("services")
    pkg.__path__ = [str(_BACKEND_DIR / "services")]
    sys.modules.setdefault("services", pkg)

    def is_founder_email(email: str) -> bool:
        calls["founder_checks"].append(email)
        return (email or "").strip().lower() == FOUNDER_EMAIL
    usage = _delegating_fake("services.usage",
                             is_founder_email=is_founder_email)
    sys.modules["services.usage"] = usage

    mfa = _delegating_fake(
        "services.mfa",
        verify_code=lambda secret, code: False,
        consume_backup_code=lambda code, hashes: (False, hashes),
    )
    sys.modules["services.mfa"] = mfa

    async def _allow(*a, **k):
        return True
    rl = _delegating_fake(
        "services.rate_limiter",
        check_rate_limit_async=_allow,
        client_ip_from_request=lambda request: "127.0.0.1",
    )
    sys.modules["services.rate_limiter"] = rl

    async def _persist_turn(**kwargs):
        calls["persist_turn"].append(kwargs)
    chat_helpers = _delegating_fake("services.chat_helpers",
                                    _persist_turn=_persist_turn)
    sys.modules["services.chat_helpers"] = chat_helpers

    async def _no_member(user_id):
        return None
    admin_team = _delegating_fake(
        "services.admin_team",
        get_active_member=_no_member,
        effective_level=lambda *a, **k: 0,
    )
    sys.modules["services.admin_team"] = admin_team
    return calls


CALLS = _install_service_stubs()

import cto_services.auth as auth
import cto_services.db as dbmod
import routers.admin_context_bridge as acb
import routers.auth as rauth

SECRET = os.environ["JWT_SECRET"]


# ── Fake Mongo surface ──────────────────────────────────────────────
class _FakeColl:
    def __init__(self, find_one_result=None, find_one_exc=None):
        self._result = find_one_result
        self._exc = find_one_exc
        self.inserted: list = []
        self.update_calls: list = []

    async def find_one(self, filt, proj=None):
        if self._exc is not None:
            raise self._exc
        if callable(self._result):
            return self._result(filt)
        return self._result

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return types.SimpleNamespace(inserted_id="fake-id")

    async def update_one(self, filt, update, upsert=False):
        self.update_calls.append((filt, update))
        return types.SimpleNamespace(modified_count=1)


class _FakeDB:
    """Enough of Motor's Database for current_dev + the bridge checks.

    dev_users.find_one always returns the founder row (with an optional
    session_barrier_at); db["revoked_tokens"].find_one answers from a
    set of revoked jtis (or raises, for the fail-closed test).
    """

    def __init__(self, barrier=None, revoked_jtis=frozenset(),
                 revoked_exc=None):
        self._row = {
            "user_id": USER_ID,
            "email": FOUNDER_EMAIL,
            "tier": "founder",
            "is_admin": True,
            "is_founder": True,
            "plan": "founder",
            "plan_limit": 0,
            "is_unlimited": True,
        }
        if barrier is not None:
            self._row["session_barrier_at"] = barrier
        self.dev_users = _FakeColl(find_one_result=self._row)
        self._revoked = _FakeColl(
            find_one_result=lambda filt: (
                {"jti": filt.get("jti")} if filt.get("jti") in revoked_jtis else None
            ),
            find_one_exc=revoked_exc,
        )
        self.ora_context_bridge_snapshots = _FakeColl()

    def __getitem__(self, name):
        if name == "revoked_tokens":
            return self._revoked
        raise KeyError(name)


def _session_token(**overrides) -> str:
    now = int(time.time())
    payload = {
        "user_id": USER_ID,
        "email": FOUNDER_EMAIL,
        "is_admin": True,
        "mfa": True,
        "iat": now,
        "jti": "session-jti-aaa",
        "exp": now + 86400 * 7,
    }
    payload.update(overrides)
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _bridge_token(session_jti="session-jti-aaa", session_iat=None, **overrides) -> str:
    if session_iat is None:
        session_iat = int(time.time())
    return auth.create_context_bridge_token(
        USER_ID, FOUNDER_EMAIL, session_jti, session_iat,
        is_admin=True, mfa_verified=True,
    ) if not overrides else _custom_bridge_token(session_jti, session_iat, **overrides)


def _custom_bridge_token(session_jti, session_iat, **overrides) -> str:
    now = int(time.time())
    payload = {
        "user_id": USER_ID,
        "email": FOUNDER_EMAIL,
        "is_admin": True,
        "mfa": True,
        "scope": "context_bridge",
        "session_jti": session_jti,
        "session_iat": session_iat,
        "iat": now,
        "jti": "bridge-jti-bbb",
        "exp": now + 900,
    }
    payload.update(overrides)
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def _fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(dbmod, "get_db", lambda: db)
    monkeypatch.setattr(acb, "get_db", lambda: db)
    return db


# ── create_context_bridge_token ─────────────────────────────────────
def test_mint_shape_15_min_scoped_bound_to_session():
    now = int(time.time())
    tok = auth.create_context_bridge_token(
        USER_ID, FOUNDER_EMAIL, "sess-jti", 1234567,
        is_admin=True, mfa_verified=True,
    )
    p = jwt.decode(tok, SECRET, algorithms=["HS256"])
    assert p["scope"] == "context_bridge"
    assert p["exp"] - p["iat"] == 900
    assert abs(p["exp"] - (now + 900)) <= 5
    assert p["session_jti"] == "sess-jti"
    assert p["session_iat"] == 1234567
    assert p["mfa"] is True
    assert p["is_admin"] is True
    assert p["jti"] and p["jti"] != "sess-jti"


def test_mint_preserves_mfa_false():
    tok = auth.create_context_bridge_token(
        USER_ID, FOUNDER_EMAIL, "sess-jti", 1234567, mfa_verified=False
    )
    assert jwt.decode(tok, SECRET, algorithms=["HS256"])["mfa"] is False


# ── require_context_bridge_token ────────────────────────────────────
@pytest.mark.asyncio
async def test_bridge_token_accepted():
    user = await auth.require_context_bridge_token(
        f"Bearer {_bridge_token()}")
    assert user["user_id"] == USER_ID
    assert user["scope"] == "context_bridge"


@pytest.mark.asyncio
async def test_session_jwt_rejected_wrong_scope():
    with pytest.raises(HTTPException) as ei:
        await auth.require_context_bridge_token(f"Bearer {_session_token()}")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_bridge_token_rejected():
    tok = _custom_bridge_token("session-jti-aaa", int(time.time()),
                               exp=int(time.time()) - 10)
    with pytest.raises(HTTPException) as ei:
        await auth.require_context_bridge_token(f"Bearer {tok}")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_scope_token_rejected():
    tok = _custom_bridge_token("session-jti-aaa", int(time.time()),
                               scope="other_scope")
    with pytest.raises(HTTPException) as ei:
        await auth.require_context_bridge_token(f"Bearer {tok}")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_mfa_pending_token_rejected():
    pending = auth.create_mfa_pending_token(USER_ID, FOUNDER_EMAIL)
    with pytest.raises(HTTPException) as ei:
        await auth.require_context_bridge_token(f"Bearer {pending}")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_revoked_parent_session_rejected(monkeypatch):
    db = _FakeDB(revoked_jtis={"session-jti-aaa"})
    monkeypatch.setattr(dbmod, "get_db", lambda: db)
    with pytest.raises(HTTPException) as ei:
        await auth.require_context_bridge_token(
            f"Bearer {_bridge_token()}")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_barriered_parent_session_rejected(monkeypatch):
    # Barrier AFTER the session's iat => parent session is dead.
    db = _FakeDB(barrier=int(time.time()) + 60)
    monkeypatch.setattr(dbmod, "get_db", lambda: db)
    with pytest.raises(HTTPException) as ei:
        await auth.require_context_bridge_token(
            f"Bearer {_bridge_token(session_iat=int(time.time()) - 3600)}")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_db_error_fail_closed_503(monkeypatch):
    db = _FakeDB(revoked_exc=RuntimeError("mongo down"))
    monkeypatch.setattr(dbmod, "get_db", lambda: db)
    with pytest.raises(HTTPException) as ei:
        await auth.require_context_bridge_token(
            f"Bearer {_bridge_token()}")
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_db_none_503(monkeypatch):
    monkeypatch.setattr(dbmod, "get_db", lambda: None)
    with pytest.raises(HTTPException) as ei:
        await auth.require_context_bridge_token(
            f"Bearer {_bridge_token()}")
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_missing_auth_header_401():
    with pytest.raises(HTTPException) as ei:
        await auth.require_context_bridge_token(None)
    assert ei.value.status_code == 401


# ── POST /auth/context-bridge-token ─────────────────────────────────
def _mint_app() -> TestClient:
    app = FastAPI()
    app.include_router(rauth.router)
    return TestClient(app, raise_server_exceptions=False)


def test_mint_endpoint_returns_15_min_scoped_token():
    client = _mint_app()
    r = client.post("/auth/context-bridge-token",
                    headers={"Authorization": f"Bearer {_session_token()}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "context_bridge"
    assert body["expires_in"] == 900
    p = jwt.decode(body["token"], SECRET, algorithms=["HS256"])
    assert p["scope"] == "context_bridge"
    assert p["exp"] - p["iat"] == 900
    assert p["session_jti"] == "session-jti-aaa"
    assert p["mfa"] is True


def test_mint_endpoint_rejects_bridge_token_as_input():
    client = _mint_app()
    r = client.post("/auth/context-bridge-token",
                    headers={"Authorization": f"Bearer {_bridge_token()}"})
    assert r.status_code == 401


def test_mint_endpoint_rejects_expired_session():
    client = _mint_app()
    expired = _session_token(exp=int(time.time()) - 10)
    r = client.post("/auth/context-bridge-token",
                    headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


def test_mint_endpoint_rejects_pre_jti_session():
    client = _mint_app()
    old_style = jwt.encode(
        {"user_id": USER_ID, "email": FOUNDER_EMAIL, "is_admin": True,
         "exp": int(time.time()) + 86400},
        SECRET, algorithm="HS256",
    )
    r = client.post("/auth/context-bridge-token",
                    headers={"Authorization": f"Bearer {old_style}"})
    assert r.status_code == 401


def test_mint_endpoint_rejects_non_admin(monkeypatch):
    # Non-founder, no team membership => require_admin 403s.
    db = _FakeDB()
    db._row["email"] = "staff@example.com"  # in place: the coll holds this dict
    monkeypatch.setattr(dbmod, "get_db", lambda: db)
    staff_token = _session_token(email="staff@example.com")
    client = _mint_app()
    r = client.post("/auth/context-bridge-token",
                    headers={"Authorization": f"Bearer {staff_token}"})
    assert r.status_code == 403


def test_mint_endpoint_rejects_no_auth():
    client = _mint_app()
    assert client.post("/auth/context-bridge-token").status_code == 401


# ── POST /admin/ora/context-bridge handler ──────────────────────────
@pytest.mark.asyncio
async def test_bridge_handler_accepts_bridge_token():
    payload = acb.ContextBridgePayload(
        session_id="sess-1", url="https://vendor.example/x", title="Vendor",
        visible_text="hello", console_errors=["boom"],
        network_metrics=[], captured_at="2026-11-25T00:00:00Z",
    )
    before = len(CALLS["persist_turn"])
    res = await acb.receive_context_bridge(
        payload, authorization=f"Bearer {_bridge_token()}")
    assert res["ok"] is True
    assert len(CALLS["persist_turn"]) == before + 1
    assert CALLS["persist_turn"][-1]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_bridge_handler_rejects_session_jwt():
    payload = acb.ContextBridgePayload(
        session_id="sess-1", url="https://vendor.example/x", title="Vendor",
        visible_text="hello", console_errors=[],
        network_metrics=[], captured_at="2026-11-25T00:00:00Z",
    )
    with pytest.raises(HTTPException) as ei:
        await acb.receive_context_bridge(
            payload, authorization=f"Bearer {_session_token()}")
    assert ei.value.status_code == 401
