"""
tests/test_2026_02_project_brain_trigger_audit.py — Gap 2 follow-up
(2026-02, founder push-back on the initial "working as designed"
finding)

Founder's exact ask: "show the actual count — out of N real
interactions, how many matched vs didn't, and why." This locks in:
  1. `services.project_brain.detect_brain_triggers` — the exact pure
     matching logic, extracted (no behavior change) from
     `update_brain_from_conversation` so a read-only audit can use
     the SAME function, never a second hand-copied keyword list that
     could silently drift out of sync with what actually writes.
  2. `GET /admin/project-brain/{project_id}/trigger-audit` — read-only,
     re-runs that same function against every real logged turn in
     `ora_council_logs` for a project (no writes to project_brains),
     returns the real match/no-match counts + sample misses.
"""
from services.project_brain import detect_brain_triggers


def test_no_keyword_or_phrase_present_matches_nothing():
    out = detect_brain_triggers(
        "what does the health function in drill/rollback_drill_target.py return",
        "it returns 'ok'",
    )
    assert out["stack"] == []
    assert out["decision"] is False
    assert out["rejected"] is False


def test_stack_keyword_in_reply_alone_still_matches():
    # The keyword doesn't have to be in the USER's message — it also
    # checks ORA's own reply (e.g. "this looks like a FastAPI route").
    out = detect_brain_triggers("what framework is this", "this is a FastAPI route")
    assert "fastapi" in out["stack"]


def test_decision_phrase_matches():
    out = detect_brain_triggers("we decided to use MongoDB here", "Noted.")
    assert out["decision"] is True
    assert "mongodb" in out["stack"]


def test_rejection_phrase_matches():
    out = detect_brain_triggers("we decided against GraphQL, avoid it", "OK.")
    assert out["rejected"] is True


async def test_trigger_audit_endpoint_counts_real_rows(monkeypatch):
    """Exercises the actual admin_project_brain_trigger_audit()
    handler against a fake ora_council_logs cursor with a known,
    hand-picked mix — 2 technical (no match) + 2 decision-phrase
    turns — and asserts the returned counts are exactly right."""
    from routers.admin_projects_brain import admin_project_brain_trigger_audit
    import routers.admin_projects_brain as mod

    rows = [
        {"user_message": "what does health() return", "final_output": "it returns ok"},
        {"user_message": "why is this endpoint slow", "final_output": "no obvious cause"},
        {"user_message": "we decided to use Redis here", "final_output": "got it"},
        {"user_message": "let's go with Postgres instead", "final_output": "noted"},
    ]

    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows
        def sort(self, *a, **k):
            return self
        def limit(self, *a, **k):
            return self
        def __aiter__(self):
            return iter(self._rows).__iter__()

    class _FakeCollection:
        def find(self, *a, **k):
            return _FakeAsyncIter(rows)

    class _FakeAsyncIter:
        def __init__(self, rows):
            self._rows = list(rows)
        def sort(self, *a, **k):
            return self
        def limit(self, *a, **k):
            return self
        def __aiter__(self):
            self._it = iter(self._rows)
            return self
        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _FakeDB:
        ora_council_logs = _FakeCollection()

    monkeypatch.setattr(mod, "_require_admin", lambda auth: _noop())
    monkeypatch.setattr(mod, "require_db", lambda: _FakeDB())

    async def _noop():
        return None

    result = await admin_project_brain_trigger_audit(
        project_id="p_test", limit=2000, authorization="Bearer x",
    )
    assert result["total_turns_found"] == 4
    assert result["matched_a_trigger"] == 2
    assert result["no_trigger_matched"] == 2
    assert result["match_rate_pct"] == 50.0
    assert result["breakdown"]["decision_phrase_hits"] == 2
    assert len(result["sample_non_matching_messages"]) == 2
