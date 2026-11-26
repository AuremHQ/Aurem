"""test_audit_p1e_b9_history_feedback.py — P1 audit fix B9 (2026-11-25).

B9, two parts, both proved here against the REAL functions with a
fake in-memory Mongo (p1e_stubs.FakeDB — implements the exact update
operators the code uses, including Mongo's null-padding on an
out-of-range $set index):
  1. $slice mismatch: chat_task_followup() persisted with $slice -40
     while _persist_turn() uses -200 (Iter 329). Both now share the
     CHAT_TURNS_RETENTION constant (= 200), so they cannot drift
     apart again.
  2. Feedback growth: chat_feedback() wrote turns.{i}.feedback via
     $set with no length cap on `comment` and no bounds check on
     `turn_index` — Mongo silently creates sparse turns[N] entries
     on an out-of-range $set (the Iter 34 note on /turn/shipped),
     and the old code returned ok True even when the update matched
     nothing. Now: comment capped at 2000 chars (Pydantic + write
     truncation), turn_index bounds-checked against the live turns
     array (400 when out of range), 404 when the session is gone or
     the update matched nothing.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1e_stubs  # noqa: E402

p1e_stubs.install_p1e_stubs()

from routers.chat import misc as misc_mod  # noqa: E402
from services import chat_helpers as ch  # noqa: E402

USER = {"user_id": "u1"}


def _turns(n, role="assistant"):
    return [{"role": role, "content": f"turn {i}", "ts": 1000 + i}
            for i in range(n)]


def _session_db(n_turns=3, session_id="s1"):
    return p1e_stubs.FakeDB(
        chat_sessions=[{"session_id": session_id, "user_id": "u1",
                        "turns": _turns(n_turns)}],
    )


async def _fake_current_dev(authorization=None):
    return dict(USER)


def _run(coro):
    return asyncio.run(coro)


# ── 1. The shared retention constant ────────────────────────────────
def test_b9_both_writers_share_one_retention_constant():
    """The -40/-200 drift is closed structurally, not just by
    editing a literal: both writers import the same constant."""
    assert ch.CHAT_TURNS_RETENTION == 200
    assert misc_mod.CHAT_TURNS_RETENTION == 200
    assert misc_mod.CHAT_TURNS_RETENTION is ch.CHAT_TURNS_RETENTION


def test_b9_persist_turn_writes_slice_minus_200(monkeypatch):
    """_persist_turn's $push still carries $slice -200 (now via the
    constant) — the documented retention is unchanged."""
    db = _session_db(0)
    monkeypatch.setattr(ch, "get_db", lambda: db)
    _run(ch._persist_turn("u1", "s1", "hello", "reply", "ora",
                          interaction={"turn_state": "success"}))
    doc = db.chat_sessions.docs[0]
    assert len(doc["turns"]) == 2  # user + assistant


def test_b9_persist_turn_enforces_the_200_cap(monkeypatch):
    """199 stored turns + one new turn (2 pushed) = 201 → the $slice
    keeps exactly the newest 200 and drops the oldest."""
    db = p1e_stubs.FakeDB(
        chat_sessions=[{"session_id": "s1", "user_id": "u1",
                        "turns": _turns(199)}])
    monkeypatch.setattr(ch, "get_db", lambda: db)
    _run(ch._persist_turn("u1", "s1", "hello", "reply", "ora",
                          interaction={"turn_state": "success"}))
    turns = db.chat_sessions.docs[0]["turns"]
    assert len(turns) == 200
    assert turns[0]["content"] == "turn 1"  # "turn 0" fell off
    assert turns[-1]["content"] == "reply"


# ── 1b. task-followup no longer drops history at 40 ────────────────
def _blocked_task_db(n_turns=45):
    return p1e_stubs.FakeDB(
        cto_tasks=[{"task_id": "t1", "user_id": "u1", "status": "blocked",
                    "task": "fix the login bug",
                    "blocked_reason": "guard fired on auth.py"}],
        chat_sessions=[{"session_id": "s1", "user_id": "u1",
                        "turns": _turns(n_turns)}],
    )


def test_b9_task_followup_keeps_history_past_40(monkeypatch):
    """45 stored turns + 1 followup = 46. The old -40 slice kept
    only 40 (silently dropping 6 turns of history); the fixed
    writer keeps all 46."""
    db = _blocked_task_db(45)
    monkeypatch.setattr(misc_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(misc_mod, "get_db", lambda: db)
    res = _run(misc_mod.chat_task_followup(
        misc_mod.TaskFollowupBody(session_id="s1", task_id="t1"), None))
    assert res["ok"] is True
    turns = db.chat_sessions.docs[0]["turns"]
    assert len(turns) == 46
    assert turns[0]["content"] == "turn 0"  # oldest turn survives
    assert turns[-1]["kind"] == "task_followup"


def test_b9_task_followup_caps_at_200(monkeypatch):
    """250 stored turns + 1 followup: the shared cap still trims
    to exactly 200 (the fix aligns the cap, it does not remove it)."""
    db = _blocked_task_db(250)
    monkeypatch.setattr(misc_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(misc_mod, "get_db", lambda: db)
    _run(misc_mod.chat_task_followup(
        misc_mod.TaskFollowupBody(session_id="s1", task_id="t1"), None))
    turns = db.chat_sessions.docs[0]["turns"]
    assert len(turns) == 200
    assert turns[-1]["kind"] == "task_followup"


# ── 2. Feedback: comment cap ────────────────────────────────────────
def test_b9_feedback_comment_over_cap_rejected_by_model():
    """Pydantic enforces the 2000-char cap at the HTTP boundary
    (FastAPI turns this into a 422)."""
    with pytest.raises(ValidationError):
        misc_mod.FeedbackBody(session_id="s1", turn_index=0,
                              vote="up", comment="x" * 2001)


def test_b9_feedback_comment_at_cap_accepted():
    body = misc_mod.FeedbackBody(session_id="s1", turn_index=0,
                                 vote="up", comment="x" * 2000)
    assert len(body.comment) == 2000


def test_b9_feedback_long_comment_truncated_at_write(monkeypatch):
    """A direct (non-HTTP) caller bypasses Pydantic, so the write
    truncates too: a 5000-char comment lands as exactly 2000."""
    db = _session_db(3)
    monkeypatch.setattr(misc_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(misc_mod, "get_db", lambda: db)
    body = misc_mod.FeedbackBody.model_construct(
        session_id="s1", turn_index=1, vote="down", comment="y" * 5000)
    res = _run(misc_mod.chat_feedback(body, None))
    assert res == {"ok": True}
    stored = db.chat_sessions.docs[0]["turns"][1]["feedback"]
    assert stored["vote"] == "down"
    assert len(stored["comment"]) == 2000


# ── 2b. Feedback: turn_index bounds + matched check ─────────────────
def test_b9_feedback_happy_path_still_works(monkeypatch):
    db = _session_db(3)
    monkeypatch.setattr(misc_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(misc_mod, "get_db", lambda: db)
    res = _run(misc_mod.chat_feedback(
        misc_mod.FeedbackBody(session_id="s1", turn_index=1,
                              vote="up", comment="helpful"), None))
    assert res == {"ok": True}
    stored = db.chat_sessions.docs[0]["turns"][1]["feedback"]
    assert stored["vote"] == "up"
    assert stored["comment"] == "helpful"
    assert "ts" in stored
    assert "feedback" not in db.chat_sessions.docs[0]["turns"][0]


def test_b9_feedback_out_of_range_index_is_400_and_writes_nothing(
        monkeypatch):
    """turn_index 99 on a 3-turn session: 400, and the doc is
    untouched — no sparse null-padded turns[N] entries (the old
    code $set blindly and returned ok True)."""
    db = _session_db(3)
    monkeypatch.setattr(misc_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(misc_mod, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        _run(misc_mod.chat_feedback(
            misc_mod.FeedbackBody(session_id="s1", turn_index=99,
                                  vote="up"), None))
    assert exc.value.status_code == 400
    assert len(db.chat_sessions.docs[0]["turns"]) == 3
    assert all("feedback" not in t
               for t in db.chat_sessions.docs[0]["turns"])


def test_b9_feedback_negative_index_is_400(monkeypatch):
    db = _session_db(3)
    monkeypatch.setattr(misc_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(misc_mod, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        _run(misc_mod.chat_feedback(
            misc_mod.FeedbackBody(session_id="s1", turn_index=-1,
                                  vote="up"), None))
    assert exc.value.status_code == 400


def test_b9_feedback_missing_session_is_404(monkeypatch):
    db = p1e_stubs.FakeDB(chat_sessions=[])
    monkeypatch.setattr(misc_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(misc_mod, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        _run(misc_mod.chat_feedback(
            misc_mod.FeedbackBody(session_id="nope", turn_index=0,
                                  vote="up"), None))
    assert exc.value.status_code == 404


def test_b9_feedback_matched_nothing_is_404_not_ok_true(monkeypatch):
    """Session exists at read time but the update matches nothing
    (deleted in between): the old code returned ok True anyway."""

    class _VanishingCollection(p1e_stubs.FakeCollection):
        async def update_one(self, filt, update, upsert=False):
            return SimpleNamespace(matched_count=0, modified_count=0,
                                   upserted_id=None)

    db = p1e_stubs.FakeDB(chat_sessions=[])
    db._collections["chat_sessions"] = _VanishingCollection(
        [{"session_id": "s1", "user_id": "u1", "turns": _turns(3)}])
    monkeypatch.setattr(misc_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(misc_mod, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        _run(misc_mod.chat_feedback(
            misc_mod.FeedbackBody(session_id="s1", turn_index=0,
                                  vote="up"), None))
    assert exc.value.status_code == 404


def test_b9_feedback_bad_vote_still_400(monkeypatch):
    """Pre-existing validation untouched by the fix."""
    db = _session_db(3)
    monkeypatch.setattr(misc_mod, "current_dev", _fake_current_dev)
    monkeypatch.setattr(misc_mod, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        _run(misc_mod.chat_feedback(
            misc_mod.FeedbackBody(session_id="s1", turn_index=0,
                                  vote="meh"), None))
    assert exc.value.status_code == 400
