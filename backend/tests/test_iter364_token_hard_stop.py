"""
test_iter364_token_hard_stop.py — Iter 364 · Phase 3 (superseded 2026-09-27)

2026-09-27 — Product decision: the token counter is DECOUPLED from
customer-facing gating (see services/usage.py module docstring). This
suite now verifies the NEW contract instead of the original hard-stop:

  1. `assert_has_budget` never raises, even for an exhausted user — it
     logs a warning instead (kept for founder cost-visibility).
  2. `get_usage().is_blocked` no longer reflects token exhaustion —
     only the flat-fee monthly task-count cap gates real requests.
  3. `is_exhausted` / `used` / `remaining` are still tracked accurately
     (tracking kept, only the gate removed).
  4. Founder / unlimited accounts still bypass everything.
  5. Task-count enforcement (`assert_has_task_budget`) is untouched —
     covered by its own existing tests, not duplicated here.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.usage import (
    assert_has_budget, get_usage, PLAN_LIMITS,
)


def _db():
    from cto_services.db import set_db as _set_db
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    # Wire the app-state DB reference so `services.usage.get_usage`
    # (which calls `require_db()` internally) resolves in test context.
    _set_db(db)
    return db


async def _seed_exhausted_user(db, tier: str = "free") -> str:
    uid = f"u_iter364_tok_{secrets.token_hex(3)}"
    plan_limit = PLAN_LIMITS[tier]
    # Fresh doc with no grants.
    await db.dev_users.update_one(
        {"user_id": uid},
        {"$set": {
            "user_id": uid,
            "email":   f"{uid}@example.com",
            "tier":    tier,
            "tokens_granted": 0,
            "is_admin":       False,
            "is_unlimited":   False,
        }},
        upsert=True,
    )
    # Seed cto_tasks so the tokens_used aggregate reports >= plan_limit.
    # One row is enough — get_usage() sums $tokens_used over
    # status="done" rows for this user.
    await db.cto_tasks.insert_one({
        "task_id":     f"t_{secrets.token_hex(4)}",
        "user_id":     uid,
        "status":      "done",
        "tokens_used": plan_limit + 5,   # push a hair over the limit
        "created_at":  datetime.now(timezone.utc).timestamp(),
    })
    return uid


async def _cleanup(db, uid: str) -> None:
    await db.cto_tasks.delete_many({"user_id": uid})
    await db.dev_users.delete_one({"user_id": uid})


# ── Core enforcement ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assert_has_budget_no_longer_raises_when_exhausted(caplog):
    """2026-09-27 — assert_has_budget must NOT raise for an exhausted
    user anymore (product decision: token count no longer gates real
    requests). It must instead log a warning so we keep visibility
    into disproportionate token burn — tracking kept, gating removed."""
    db = _db()
    uid = await _seed_exhausted_user(db, "free")
    try:
        import logging
        caplog.set_level(logging.WARNING, logger="services.usage")
        await assert_has_budget(uid)  # must NOT raise
        assert any(
            "TOKEN_BUDGET_WOULD_HAVE_BLOCKED" in rec.message
            for rec in caplog.records
        ), "expected a visibility warning to be logged for the exhausted user"
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_get_usage_is_blocked_false_on_token_exhaustion_alone():
    """2026-09-27 — token exhaustion ALONE must no longer set
    is_blocked=True; only the flat-fee task-count cap does. Tracking
    fields (is_exhausted / used / remaining) stay accurate — only the
    gate signal changes."""
    db = _db()
    uid = await _seed_exhausted_user(db, "free")
    try:
        u = await get_usage(uid)
        assert u["is_exhausted"] is True
        assert u["remaining"] == 0
        assert u["is_blocked"] is False, (
            f"token exhaustion alone must not block — task-count cap is "
            f"the only gate now, got {u}"
        )
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_get_usage_is_blocked_false_after_grant():
    """Admin grants bonus tokens → effective_limit rises → is_exhausted
    flips back to False and remaining usage is restored. is_blocked was
    already False before the grant (2026-09-27 decouple), so this now
    checks the tracking fields specifically, not the gate."""
    db = _db()
    uid = await _seed_exhausted_user(db, "free")
    try:
        # Simulate what /admin/users/{uid}/grant-tokens does.
        await db.dev_users.update_one(
            {"user_id": uid},
            {"$inc": {"tokens_granted": 500_000}},
        )
        u = await get_usage(uid)
        assert u["is_blocked"] is False
        assert u["is_exhausted"] is False, (
            "grant should lift token exhaustion; usage row still says "
            f"is_exhausted=True: {u}"
        )
        # And the gate itself must still not raise.
        await assert_has_budget(uid)  # must NOT raise
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_founder_never_blocked_even_when_over_limit():
    """Founder / unlimited accounts bypass the gate regardless of
    tokens_used. Prevents an accidental block on our own internal
    QA-drift accounts."""
    db = _db()
    uid = f"u_iter364_founder_tok_{secrets.token_hex(3)}"
    await db.dev_users.update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "tier": "founder",
                  "is_unlimited": True, "is_admin": True,
                  "tokens_granted": 0}},
        upsert=True,
    )
    await db.cto_tasks.insert_one({
        "task_id":     f"t_{secrets.token_hex(4)}",
        "user_id":     uid,
        "status":      "done",
        "tokens_used": 10**9,   # comically over any plan
        "created_at":  datetime.now(timezone.utc).timestamp(),
    })
    try:
        u = await get_usage(uid)
        assert u["is_blocked"] is False
        assert u["is_unlimited"] is True
        # Gate must not raise.
        await assert_has_budget(uid)
    finally:
        await _cleanup(db, uid)


# ── Router wiring: import-time invariant ─────────────────────────────
# The Iter 364 fix's whole point is that the LLM call MUST NOT happen
# when the wallet is empty. The tightest, most-honest check is an
# AST-level assertion that `assert_has_budget` is called in every
# endpoint that then goes on to invoke an LLM. We enumerate the known
# hot endpoints here — a code refactor that removes the gate call
# will trip this.

_MUST_GATE = {
    "/app/backend/routers/chat/turn.py":   ["chat_send"],
    "/app/backend/routers/chat/stream.py": ["chat_stream"],
    "/app/backend/routers/diagram.py":  ["generate_diagram"],
    "/app/backend/routers/upload.py":   ["upload_convert"],
}


def test_llm_endpoints_still_call_assert_has_budget():
    """Static grep — every listed handler function body MUST contain
    the string `assert_has_budget`. If someone later refactors the
    call out, this test screams before the silent-burn re-appears."""
    import ast
    for path, fnames in _MUST_GATE.items():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name in fnames:
                    body_src = ast.get_source_segment(src, node) or ""
                    assert "assert_has_budget" in body_src, (
                        f"{path}::{node.name} lost its "
                        f"assert_has_budget gate — this reopens the "
                        f"Iter 364 silent-burn hole"
                    )
