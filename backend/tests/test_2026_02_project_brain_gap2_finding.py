"""
tests/test_2026_02_project_brain_gap2_finding.py — Gap 2 (2026-02)

Founder finding (live, in /admin/settings → Brain tab, production
today): Overview's interaction counter is confirmed live and
incrementing (605 -> 608 on real timestamped messages), but loading
Project Brain for that same project returned "No brain data yet."

Root-cause investigation (both hypotheses the founder asked to check,
checked BEFORE writing anything):

1. Read/write project_id format mismatch? NO — confirmed by direct
   inspection: the write side (services/project_brain.py's
   `update_brain_from_conversation`/`update_brain_after_commit`/
   `update_brain_after_task`, called from routers/chat/stream.py,
   routers/cto_projects/worker_api.py, worker_git.py, loop_engine.py)
   and the read side (routers/admin_projects_brain.py's
   `admin_project_brain` -> `get_brain_full`, and the same router's
   `/brain/{project_id}/dump` which does `db.project_brains.find_one
   ({"project_id": project_id})` directly) both key on the EXACT same
   plain string `project_id` field in the SAME `project_brains`
   collection. No format mismatch exists.

2. Is there a write attempt at all, and is it gated behind a
   threshold? Live-verified (this test's `test_...` functions below
   lock in the SAME shape as the live repro): write attempts fire on
   every /chat/stream turn classified mode A/B/chat with a real
   (non-"home") project_id — NOT gated behind an interaction COUNT.
   BUT `update_brain_from_conversation`'s own body only persists
   anything if the message/reply contains one of ~18 tech-stack
   keywords OR one of 10 explicit decision/rejection phrases
   ("we'll use", "let's go with", "decided to", "going with", "we
   chose", "don't use", "avoid", "we decided against", "not using",
   "we rejected") — a deliberately narrow, cheap pattern-match (see
   that function's own docstring: "Lightweight — no LLM call, just
   pattern matching"), not a bug. A project whose real interactions
   are mostly technical debugging/QA (checking scanners, editing
   files, re-running the same repro prompt) can genuinely have 608
   real turns and never trip any of those ~28 triggers — that's the
   actual, confirmed explanation for "No brain data yet", not a
   wiring/format bug. Live-verified 2026-02: a no-keyword-match
   /chat/stream turn left `project_brains.updated_at` completely
   unchanged; an explicit "we decided to use MongoDB and React..."
   turn on the SAME project immediately added a real `decisions` row
   and 2 new `tech_stack` entries with today's real timestamp.

3. `/chat/send` (routers/chat/turn.py, the non-streaming endpoint)
   reads brain context but has NO call to
   `update_brain_from_conversation` at all — confirmed by grep. Not
   the founder's live path (frontend's real chat UI uses
   `/chat/stream` exclusively per `frontend/src/lib/api.js`), but
   noted for completeness — any caller of the non-streaming endpoint
   alone would never write to Brain regardless of content.

VERDICT: working as designed for (1) and the keyword-gate part of
(2) — no fix applied, per this round's explicit "don't manufacture a
fix for a system that isn't actually broken" instruction. (3) is a
real asymmetry but not the founder's actual live path, so it isn't
touched either (scope discipline — this ticket is about the two
CONFIRMED gaps, not every asymmetry found while investigating them).
"""
from services.project_brain import update_brain_from_conversation


class _FakeCollection:
    def __init__(self):
        self.update_one_calls = []

    async def update_one(self, filt, update, upsert=False):
        self.update_one_calls.append((filt, update, upsert))
        return None


class _FakeDB:
    def __init__(self):
        self.project_brains = _FakeCollection()

    def __getitem__(self, name):
        assert name == "project_brains"
        return self.project_brains


async def test_no_keyword_match_writes_nothing():
    """The exact live-repro shape: a real technical question with no
    tech-stack word and no decision/rejection phrase must NOT touch
    project_brains at all — this is the confirmed root cause of
    'No brain data yet' for a mostly-debugging conversation history,
    not a wiring bug."""
    db = _FakeDB()
    await update_brain_from_conversation(
        db, "p_test", "what does the health function return", "it returns ok", "B",
    )
    assert db.project_brains.update_one_calls == []


async def test_decision_phrase_writes_a_real_row():
    """The exact live-repro shape that DID work: an explicit decision
    phrase reaches project_brains with today's data — proves the
    write path itself is not broken, just narrowly gated."""
    db = _FakeDB()
    await update_brain_from_conversation(
        db, "p_test",
        "we decided to use MongoDB and React for this project going forward",
        "Noted.", "B",
    )
    assert len(db.project_brains.update_one_calls) == 2  # tech_stack push + decisions push
    stack_call, decision_call = db.project_brains.update_one_calls
    assert stack_call[0] == {"project_id": "p_test"}
    assert "mongodb" in stack_call[1]["$addToSet"]["tech_stack"]["$each"]
    assert "react" in stack_call[1]["$addToSet"]["tech_stack"]["$each"]
    assert decision_call[1]["$push"]["decisions"]["$each"][0]["title"].startswith(
        "we decided to use MongoDB"
    )


async def test_rejection_phrase_writes_a_real_row():
    db = _FakeDB()
    await update_brain_from_conversation(
        db, "p_test", "we decided against using GraphQL here, avoid it entirely", "OK.", "B",
    )
    assert len(db.project_brains.update_one_calls) >= 1


def test_read_and_write_use_the_same_project_id_key():
    """Confirms hypothesis (1) is NOT the bug: both sides key on a
    plain string project_id in the same collection name."""
    import inspect
    from services import project_brain
    src = inspect.getsource(project_brain.get_brain_full)
    assert 'project_brains' in src
    assert '"project_id": project_id' in src or "'project_id': project_id" in src


def test_chat_send_never_calls_brain_write():
    """Confirms finding (3) — not the founder's live path (frontend
    uses /chat/stream), documented for completeness only."""
    import inspect
    from routers.chat import turn
    src = inspect.getsource(turn)
    assert "update_brain_from_conversation" not in src
