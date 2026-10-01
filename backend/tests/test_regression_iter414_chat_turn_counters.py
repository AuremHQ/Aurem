"""
Turn counters: outcomes, reasons, tool failures/retries and state answers
are counted per day, on both chat endpoints, without ever failing a turn.
"""
import asyncio
import json

import pytest
from tests._mini_motor import AsyncMongoMockClient

from services import chat_metrics


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_regression_iter414_counters_for_turn():
    result = {"provider": "state-answer", "tool_invocations": [
        {"tool": "list_issues", "ok": False, "retried": True},
        {"tool": "read_repo_file", "ok": True}]}
    inter = {"outcome": "NOTHING_DONE", "reason": "answered"}
    assert chat_metrics.counters_for_turn(result, inter) == {
        "turns.NOTHING_DONE": 1, "reasons.answered": 1,
        "tool_failures": 1, "tool_retries": 1, "state_answers": 1}
    assert chat_metrics.counters_for_turn({}, None) == {}


def test_regression_iter414_record_and_summary_accumulate_per_day():
    db = AsyncMongoMockClient()["t"]

    async def go():
        for _ in range(3):
            await chat_metrics.record_turn(db, {}, {"outcome": "DONE", "reason": "tool_result"})
        await chat_metrics.record_turn(db, {}, {"outcome": "FAILED", "reason": "turn_error"})
        await db.pending_actions.insert_one({"status": "AWAITING_CONFIRM"})
        await db.pending_actions.insert_one({"status": "VERIFIED"})
        return await chat_metrics.summary(db, days=7)

    out = _run(go())
    assert out["counters"]["turns.DONE"] == 3 and out["counters"]["turns.FAILED"] == 1
    assert out["pending_actions_by_status"]["AWAITING_CONFIRM"] == 1
    assert out["pending_actions_by_status"]["VERIFIED"] == 1


def test_regression_iter414_a_metrics_failure_never_fails_the_caller():
    class Boom:
        def __getattr__(self, _):
            raise RuntimeError("db down")
    _run(chat_metrics.record_turn(Boom(), {}, {"outcome": "DONE", "reason": "x"}))


def test_regression_iter414_chat_send_records_the_turn(monkeypatch):
    """Behavioural: a real chat_send turn writes its outcome counter."""
    import os
    os.environ["INTERACTION_CONTRACT_V1"] = "1"
    try:
        from cto_services.db import set_db
        from routers.chat import turn as turn_mod
        db = AsyncMongoMockClient()["t"]
        set_db(db)

        async def dev(*a, **k):
            return {"user_id": "u1", "email": "u@example.com", "tier": "pro"}

        async def noop(*a, **k):
            return None
        monkeypatch.setattr(turn_mod, "current_dev", dev)
        monkeypatch.setattr("services.usage.assert_has_budget", noop)
        monkeypatch.setattr("services.usage.assert_has_task_budget", noop)

        async def go():
            await turn_mod.chat_send(body=turn_mod.ChatBody(prompt="?", session_id="s"),
                                     authorization="Bearer x")
            await asyncio.sleep(0.2)
            return await db.chat_metrics.find_one({}, {"_id": 0})
        doc = _run(go())
        assert doc["turns"]["NOTHING_DONE"] == 1 and doc["state_answers"] == 1
    finally:
        os.environ.pop("INTERACTION_CONTRACT_V1", None)
