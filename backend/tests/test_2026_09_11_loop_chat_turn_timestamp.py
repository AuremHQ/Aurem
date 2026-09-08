"""
test_2026_09_11_loop_chat_turn_timestamp.py — real founder bug repro:
chat history date-bucketing + chronological order breaks after a page
refresh.

Root cause (services/loop_engine.py `_persist_chat_turns`): a Loop
task paused for human approval (plan/ship gate) can sit for hours or
days before the user resumes it. That method only fires at TERMINAL
state and previously stamped the USER's original prompt with `now`
(the resume/completion moment) instead of the loop's real start time
(`self._started_at`, captured at LoopEngine construction — i.e. the
instant the user's request was actually received). That made an
hours/days-old message look brand new: pushed to the END of
`chat_sessions.turns` (out of true chronological order relative to
anything sent in between) AND date-bucketed under "today" instead of
the day it was really sent.

Fix: the USER turn now uses `self._started_at`; the ASSISTANT reply
legitimately only exists at completion time, so it keeps `now`.
"""
from __future__ import annotations

import time

import pytest


class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def update_one(self, q, u, upsert=False):
        pushed = ((u.get("$push") or {}).get("turns") or {}).get("$each") or []
        self.rows.append({"query": q, "pushed": pushed, "set": u.get("$set")})

        class _R:
            modified_count = 1
            upserted_id = None
        return _R()


class _DB:
    def __init__(self):
        self.chat_sessions = _Coll()


def _make_engine(db):
    from services import loop_engine as le
    return le.LoopEngine(
        db=db, loop_id="lp_ts_test", user_id="u1",
        project_id="p1", user_message="regression-test the commit for X",
        session_id="sess_ts_test",
    )


@pytest.mark.asyncio
async def test_loop_chat_turn_user_ts_uses_loop_start_not_completion(monkeypatch):
    """The USER turn's `ts` must be the loop's real start time, even
    when the loop is resumed/completed much later (paused-for-approval
    repro)."""
    from services import loop_engine as le

    db = _DB()
    eng = _make_engine(db)
    real_start = eng._started_at
    assert real_start > 0

    # Simulate the loop sitting paused for a long time before the user
    # approves/resumes it — the wall clock has moved far forward by the
    # time _persist_chat_turns actually runs.
    much_later = real_start + 3 * 24 * 3600  # +3 days
    monkeypatch.setattr(le.time, "time", lambda: much_later)

    await eng._persist_chat_turns(le.LoopState.COMPLETED, {"message": "ok"})

    assert len(db.chat_sessions.rows) == 1
    pushed = db.chat_sessions.rows[0]["pushed"]
    assert len(pushed) == 2
    user_turn, assistant_turn = pushed
    assert user_turn["role"] == "user"
    assert assistant_turn["role"] == "assistant"

    # The core fix: user turn's ts is the REAL start time, not "now".
    assert user_turn["ts"] == real_start
    assert user_turn["ts"] != much_later
    # The assistant's reply genuinely only exists now — ts stays "now".
    assert assistant_turn["ts"] == much_later


@pytest.mark.asyncio
async def test_loop_chat_turn_fires_once(monkeypatch):
    """_chat_turns_saved guard must still prevent a duplicate push on
    a second terminal-state call (unrelated to this fix, but a cheap
    regression check since we touched this method)."""
    from services import loop_engine as le

    db = _DB()
    eng = _make_engine(db)
    await eng._persist_chat_turns(le.LoopState.COMPLETED, {"message": "ok"})
    await eng._persist_chat_turns(le.LoopState.COMPLETED, {"message": "ok again"})
    assert len(db.chat_sessions.rows) == 1


# ── GET /chat/history's defensive sort (backend belt-and-suspenders,
# mirrors frontend's sortMessagesByTimestamp) ──────────────────────

def test_sort_turns_by_ts_restores_chronological_order():
    from routers.chat.history import sort_turns_by_ts

    turns = [
        {"role": "user", "content": "brand new", "ts": 2000.0},
        {"role": "assistant", "content": "brand new reply", "ts": 2001.0},
        # A stale Loop turn-pair pushed LAST but with an OLDER real ts.
        {"role": "user", "content": "old loop prompt", "ts": 1000.0},
        {"role": "assistant", "content": "loop complete", "ts": 2002.0},
    ]
    sorted_turns = sort_turns_by_ts(turns)
    assert [t["content"] for t in sorted_turns] == [
        "old loop prompt", "brand new", "brand new reply", "loop complete",
    ]


def test_sort_turns_by_ts_stable_for_ties_and_missing():
    from routers.chat.history import sort_turns_by_ts

    shared_ts = 500.0
    turns = [
        {"role": "user", "content": "u1", "ts": shared_ts},
        {"role": "assistant", "content": "a1", "ts": shared_ts},
        {"role": "assistant", "content": "legacy-no-ts"},
        {"role": "user", "content": "later", "ts": 999.0},
    ]
    sorted_turns = sort_turns_by_ts(turns)
    assert [t["content"] for t in sorted_turns] == ["u1", "a1", "legacy-no-ts", "later"]
