"""tests/test_regression_iter2026_10m_loop_start_entitlement_gate.py

Closing-the-bypass-doors audit — Step 1.

`POST /loop/start` (routers/loop.py::start_loop) reaches the exact same
paid worker as the manual Fix flow (plan -> execute -> verify -> scan ->
ship) but had no fix_entitlement check at all — it had a kill-switch,
tier gate, circuit breaker and burst limiter, but nothing tied to
payment. Fixed by adding `assert_fix_entitled` right before the loop
lock/engine are created (synchronous, so an unentitled caller gets a
clean 402 instead of a loop that starts and silently fails later).

Strategy: call `start_loop` directly (not via TestClient) with a
"team"-tier user (passes `is_user_allowed` without needing founder
bypass) and a FakeDB with every collection auto-created empty, so every
pre-existing gate (kill switch, circuit breaker, concurrency cap, burst
limiter, intent/ambiguity gates) clears naturally. `acquire_loop_lock`
is mocked to fail fast with 409 AFTER my gate — the same "short-circuit
right after the point under test" trick used by
test_iter212m130_loop_founder_gate.py — so the test never has to spin
up the real engine.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def find_one(self, query=None, projection=None, sort=None):
        query = query or {}
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return dict(r)
        return None

    async def find_one_and_update(self, query, update, projection=None, **kw):
        return None

    async def update_one(self, query, update, upsert=False):
        class _R:
            matched_count = 0
            modified_count = 0
            upserted_id = None
        return _R()

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "fake"
        return _R()

    async def insert_many(self, docs):
        self.rows.extend(dict(d) for d in docs)

    async def count_documents(self, query):
        return 0

    def find(self, query=None, projection=None, sort=None, limit=None):
        class _Cur:
            async def to_list(self, length=None):
                return []
        return _Cur()

    def aggregate(self, pipeline):
        class _AggCur:
            async def to_list(self, length=None):
                return []
        return _AggCur()


class _FakeDB:
    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll


_fake_req = type("R", (), {
    "client": type("C", (), {"host": "127.0.0.1"})(),
    "headers": {},
    "url": type("U", (), {"path": "/api/aurem-dev/loop/start"})(),
})()

_TEAM_USER = {"user_id": "u_team", "email": "t@aurem.dev", "tier": "team",
              "is_admin": False, "is_unlimited": False}


async def _run_up_to_lock(monkeypatch, *, entitled: bool):
    """Drives start_loop for a team-tier user through every pre-existing
    gate, with `acquire_loop_lock` mocked to fail fast (409) right after
    the point under test. Returns the raised HTTPException."""
    from routers import loop as loop_router

    async def fake_current_dev(_auth):
        return _TEAM_USER

    fake_db = _FakeDB()
    if entitled:
        fake_db.dev_users.rows.append({**_TEAM_USER, "fix_entitlement": True})
    else:
        fake_db.dev_users.rows.append(dict(_TEAM_USER))

    monkeypatch.setattr(loop_router, "current_dev", fake_current_dev)
    monkeypatch.setattr(loop_router, "get_db", lambda: fake_db)

    from services import loop_safety
    async def fake_acquire(*_a, **_kw):
        return False, {"loop_id": "lp_existing"}
    monkeypatch.setattr(loop_safety, "acquire_loop_lock", fake_acquire)
    async def fake_circuit(*_a, **_kw):
        return (False, 0, 0)
    monkeypatch.setattr(loop_safety, "is_loop_circuit_open", fake_circuit)

    body = loop_router.StartBody(
        user_message="fix the login validation bug in auth.py",
    )
    with pytest.raises(HTTPException) as exc_info:
        await loop_router.start_loop(body=body, request=_fake_req, authorization=None)
    return exc_info.value


class TestLoopStartEntitlementGate:
    async def test_on_mode_402_when_not_entitled(self, monkeypatch):
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        exc = await _run_up_to_lock(monkeypatch, entitled=False)
        assert exc.status_code == 402, exc.detail
        assert exc.detail["code"] == "payment_required"

    async def test_on_mode_clears_gate_when_entitled(self, monkeypatch):
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        exc = await _run_up_to_lock(monkeypatch, entitled=True)
        # 409 == loop_already_running, the NEXT safety check after my
        # gate (mocked to fail fast) — proof the entitlement gate itself
        # cleared. Any 402 here would be a regression.
        assert exc.status_code == 409, exc.detail

    async def test_shadow_mode_never_blocks(self, monkeypatch):
        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        exc = await _run_up_to_lock(monkeypatch, entitled=False)
        assert exc.status_code == 409, "shadow mode must never 402"

    async def test_off_mode_default_behavior_unaffected(self, monkeypatch):
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        exc = await _run_up_to_lock(monkeypatch, entitled=False)
        assert exc.status_code == 409, "off mode must never 402"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
