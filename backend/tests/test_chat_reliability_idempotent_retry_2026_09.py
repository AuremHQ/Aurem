"""test_chat_reliability_idempotent_retry_2026_09.py —
INTERACTION_CONTRACT_V1 P1 follow-up: durable request ledger + idempotent
retry replay (see services/chat_reliability.py's module docstring).

Covers, against a REAL Mongo test database (uniquely-prefixed
request_ids, cleaned up in the fixture's teardown — no mocked DB, no
mongomock):
  1. record_started() persists a `started` row BEFORE any provider/tool
     call — the durable "task accepted, not silently lost" artifact.
     find_stale_started() proves this row survives even if nothing
     else ever runs again (simulating a hard process crash).
  2. record_completed() with turn_state="success" -> lookup_retry()
     DOES return the cached record (safe to replay verbatim).
  3. record_completed() with turn_state="error" -> lookup_retry() does
     NOT return it (a failed original must get a genuinely fresh
     retry, not the same cached failure replayed forever).
  4. record_cancelled() / record_unknown() -> lookup_retry() does NOT
     return either (only a `completed`+non-error record is ever safe
     to replay).
  5. lookup_retry() is scoped to (request_id, session_id, user_id) —
     a different session/user can never read another's cached turn.
  6. Never-recorded / wrong request_id -> lookup_retry() returns None
     (caller proceeds with a fresh attempt, exactly like an unpatched
     client that never sends `retry_of_request_id` at all).
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services import chat_reliability as cr

pytestmark = pytest.mark.asyncio


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


@pytest.fixture
async def db():
    d = _db()
    yield d


@pytest.fixture
def rid():
    return f"test-retry-{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
async def _cleanup(rid):
    yield
    d = _db()
    await d.chat_request_ledger.delete_many({"request_id": {"$regex": "^test-retry-"}})


async def test_record_started_persists_before_any_work(db, rid):
    await cr.record_started(
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    row = await db.chat_request_ledger.find_one({"request_id": rid})
    assert row is not None
    assert row["status"] == "started"
    assert row["stream_state"] == "started"


async def test_stale_started_row_survives_a_simulated_crash(db, rid):
    """Simulates a worker crash: record_started() ran, nothing else
    ever runs again. find_stale_started() must still be able to see
    it — this IS the "task not silently lost" durability proof."""
    await cr.record_started(
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    # Backdate created_at so it's past the staleness window without
    # sleeping the test suite for 240s.
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(seconds=999)).isoformat()
    await db.chat_request_ledger.update_one(
        {"request_id": rid}, {"$set": {"created_at": old}})
    stale = await cr.find_stale_started(db, older_than_s=240)
    assert any(r["request_id"] == rid for r in stale)


async def test_record_started_is_idempotent_upsert(db, rid):
    """A second record_started() for the SAME request_id must not
    overwrite an already-progressing/terminal row (it uses
    $setOnInsert) — this matters because a retried SSE reconnect could
    plausibly call it twice for what is logically one accepted task."""
    await cr.record_started(
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    await cr.record_completed(
        db, request_id=rid, content="the answer", provider="test-provider",
        tool_calls_run=2, turn_state="success",
    )
    await cr.record_started(  # called again — must NOT clobber "completed"
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    row = await db.chat_request_ledger.find_one({"request_id": rid})
    assert row["status"] == "completed"


async def test_successful_retry_is_replayed_verbatim(db, rid):
    await cr.record_started(
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    await cr.record_completed(
        db, request_id=rid, content="the real answer", provider="claude",
        tool_calls_run=3, turn_state="success",
    )
    rec = await cr.lookup_retry(
        db, retry_of_request_id=rid, session_id="sess-1", user_id="user-1")
    assert rec is not None
    assert rec["content"] == "the real answer"
    assert rec["tool_calls_run"] == 3


async def test_failed_original_is_never_replayed(db, rid):
    await cr.record_started(
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    await cr.record_completed(
        db, request_id=rid, content="Sorry, I hit an error.",
        provider="claude", tool_calls_run=0, turn_state="error",
    )
    rec = await cr.lookup_retry(
        db, retry_of_request_id=rid, session_id="sess-1", user_id="user-1")
    assert rec is None, (
        "a failed/error original must never be replayed — the user "
        "needs a genuinely fresh attempt, not the same failure again"
    )


async def test_cancelled_original_is_never_replayed(db, rid):
    await cr.record_started(
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    await cr.record_cancelled(
        db, request_id=rid, reason="hard_deadline_exceeded",
        partial_content="partial...", tool_calls_run=1,
    )
    rec = await cr.lookup_retry(
        db, retry_of_request_id=rid, session_id="sess-1", user_id="user-1")
    assert rec is None


async def test_unknown_outcome_original_is_never_replayed(db, rid):
    await cr.record_started(
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    await cr.record_unknown(db, request_id=rid, reason="worker_exception")
    rec = await cr.lookup_retry(
        db, retry_of_request_id=rid, session_id="sess-1", user_id="user-1")
    assert rec is None, (
        "Section 3 of the reliability blueprint: a timeout/exception "
        "does not prove a side effect did NOT happen — an `unknown` "
        "outcome must never be silently treated as safe to replay"
    )


async def test_lookup_retry_scoped_to_session_and_user(db, rid):
    await cr.record_started(
        db, request_id=rid, session_id="sess-A", user_id="user-A",
        project_id=None, prompt="hello", route="chat_stream",
    )
    await cr.record_completed(
        db, request_id=rid, content="user A's answer", provider="claude",
        tool_calls_run=1, turn_state="success",
    )
    # A different user (even with the SAME request_id, e.g. a guessed
    # or replayed id) must never read user A's cached turn.
    other_user = await cr.lookup_retry(
        db, retry_of_request_id=rid, session_id="sess-A", user_id="user-B")
    other_session = await cr.lookup_retry(
        db, retry_of_request_id=rid, session_id="sess-B", user_id="user-A")
    same = await cr.lookup_retry(
        db, retry_of_request_id=rid, session_id="sess-A", user_id="user-A")
    assert other_user is None
    assert other_session is None
    assert same is not None


async def test_unknown_request_id_returns_none(db):
    rec = await cr.lookup_retry(
        db, retry_of_request_id="test-retry-never-existed-xyz",
        session_id="sess-1", user_id="user-1")
    assert rec is None


async def test_missing_retry_of_request_id_is_a_noop(db):
    """An unpatched/older client never sends `retry_of_request_id` at
    all — must behave exactly like "no cached record exists"."""
    rec = await cr.lookup_retry(
        db, retry_of_request_id="", session_id="sess-1", user_id="user-1")
    assert rec is None


async def test_unknown_status_row_is_never_replayed(db, rid):
    """The reconciler (chat_ledger_reconciler_loop, wired into
    main.py's lifespan startup) marks long-stuck `started` rows
    `unknown` rather than guessing `completed`/`cancelled` — a live
    repro during this implementation found real rows stuck at
    `started` for 240s+ (client disconnected while the platform was
    still waiting on the worker; see the module docstring above
    `chat_ledger_reconciler_loop`). `lookup_retry()` must treat
    `unknown` exactly like `cancelled`/`failed` — never replay it."""
    await cr.record_started(
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    # Simulate the reconciler's own $set (mirrors chat_ledger_
    # reconciler_loop's exact update shape, without waiting 240s+ in
    # a test).
    await db.chat_request_ledger.update_one(
        {"request_id": rid},
        {"$set": {"status": "unknown", "stream_state": "unknown",
                   "reason": "stale_started_reconciled_by_sweeper"}},
    )
    rec = await cr.lookup_retry(
        db, retry_of_request_id=rid, session_id="sess-1", user_id="user-1")
    assert rec is None


async def test_reconciler_marks_only_rows_past_the_staleness_window(db, rid):
    """find_stale_started() (the reconciler's own query) must NOT
    pick up a row that is still genuinely fresh/in-flight — only
    marking rows the reconciler is confident have been abandoned."""
    await cr.record_started(
        db, request_id=rid, session_id="sess-1", user_id="user-1",
        project_id=None, prompt="hello", route="chat_stream",
    )
    fresh = await cr.find_stale_started(db, older_than_s=240)
    assert not any(r["request_id"] == rid for r in fresh), (
        "a just-created `started` row must never be swept as stale"
    )
