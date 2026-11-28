"""2026-11-27 — Ask Advisor leak fix (Issue 1) + read-only propose gate (Issue 8b).

The bug: the Ask Advisor panel and the main chat shared one session id
(AskAdvisorReal reuses the main session id, FloatingORAButton mints
`ora-panel-*` ids), both POST /chat/stream, and `_persist_turn` saved
every turn into db.chat_sessions with no tag — so advisor turns showed
up in the main window's history, in "Previous chats", and as prior-turn
context for the main chat.

The fix (smallest change):
  - `_persist_turn` (services/chat_helpers.py) tags user + assistant
    turns with `"ora_panel": True` when the turn came from the advisor,
    and does not let an advisor turn overwrite the session's
    `last_message` preview.
  - `_persist_turn` never calls `commit_boundary.propose_from_turn`
    for advisor turns or for tool-free (`tier == "casual"`) turns.
  - GET /chat/history filters out tagged turns (old turns without the
    field keep showing — fail-open for history).
  - GET /chat/sessions excludes `ora-panel-*` session ids in the query
    (combined via $and with the existing prod-e2e-* exclusion, so the
    list still returns 20 real sessions).
  - `prior_turn_context_text` / `prior_turn_had_fix_signal` scan
    backwards past advisor turns to the last MAIN-chat assistant turn.

Pure unit tests — fake in-memory DB only, no live Mongo, no network.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


# ── fakes ────────────────────────────────────────────────────────────

class _FakeUpdateResult:
    matched_count = 1
    modified_count = 1
    upserted_id = None


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *a, **kw):
        return self

    def limit(self, n):
        return self

    async def to_list(self, length=None):
        return self._docs[:length] if length else list(self._docs)


class _FakeSessions:
    """Stand-in for db.chat_sessions: find_one / update_one / find."""
    def __init__(self, doc=None, sessions=None):
        self.doc = doc
        self.sessions = sessions or []
        self.update_calls = []
        self.find_queries = []

    async def find_one(self, q, *a, **kw):
        return self.doc

    async def update_one(self, filt, upd, *a, **kw):
        self.update_calls.append((filt, upd))
        return _FakeUpdateResult()

    def find(self, q, *a, **kw):
        self.find_queries.append(q)
        return _FakeCursor(self.sessions)


class _FakeDB:
    def __init__(self, doc=None, sessions=None):
        self.chat_sessions = _FakeSessions(doc=doc, sessions=sessions)


def _patch_persist_deps(monkeypatch, fake_db):
    """_persist_turn reads the DB via get_db() and fires a summary
    update as a background task — both stubbed here."""
    monkeypatch.setattr("services.chat_helpers.get_db", lambda: fake_db)
    import services.session_summary as ss_mod
    monkeypatch.setattr(ss_mod, "maybe_update_summary", AsyncMock(return_value=None))


# ── is_advisor_turn ──────────────────────────────────────────────────

class TestIsAdvisorTurn:
    def test_ora_panel_flag(self):
        from services.chat_helpers import is_advisor_turn
        assert is_advisor_turn(SimpleNamespace(ora_panel=True, agent="auto")) is True

    def test_agent_ora(self):
        from services.chat_helpers import is_advisor_turn
        assert is_advisor_turn(SimpleNamespace(ora_panel=False, agent="ora")) is True

    def test_main_chat_is_not_advisor(self):
        from services.chat_helpers import is_advisor_turn
        assert is_advisor_turn(SimpleNamespace(ora_panel=False, agent="auto")) is False
        assert is_advisor_turn(SimpleNamespace()) is False


# ── _persist_turn tags advisor turns ─────────────────────────────────

class TestPersistTurnTagsAdvisor:
    @pytest.mark.asyncio
    async def test_advisor_turn_tagged_and_last_message_untouched(self, monkeypatch):
        from services.chat_helpers import _persist_turn
        fake_db = _FakeDB()
        _patch_persist_deps(monkeypatch, fake_db)
        recorder = AsyncMock()
        monkeypatch.setattr("services.commit_boundary.propose_from_turn", recorder)

        await _persist_turn(
            "u1", "s1", "advisor question?", "advisor answer", "deepseek",
            ora_panel=True,
        )

        assert len(fake_db.chat_sessions.update_calls) == 1
        upd = fake_db.chat_sessions.update_calls[0][1]
        each = upd["$push"]["turns"]["$each"]
        assert each[0]["role"] == "user" and each[0].get("ora_panel") is True
        assert each[1]["role"] == "assistant" and each[1].get("ora_panel") is True
        # An advisor turn must not become the session's list preview.
        assert "last_message" not in upd["$set"]
        # Advisor content is not main-chat state: no pending action.
        recorder.assert_not_called()

    @pytest.mark.asyncio
    async def test_main_turn_untagged_and_proposes(self, monkeypatch):
        from services.chat_helpers import _persist_turn
        fake_db = _FakeDB()
        _patch_persist_deps(monkeypatch, fake_db)
        recorder = AsyncMock()
        monkeypatch.setattr("services.commit_boundary.propose_from_turn", recorder)

        await _persist_turn(
            "u1", "s1", "add a line to readme.md", "done", "deepseek",
            tier="agentic",
        )

        assert len(fake_db.chat_sessions.update_calls) == 1
        upd = fake_db.chat_sessions.update_calls[0][1]
        each = upd["$push"]["turns"]["$each"]
        assert "ora_panel" not in each[0] and "ora_panel" not in each[1]
        assert "last_message" in upd["$set"]
        recorder.assert_called_once()
        assert recorder.call_args.kwargs["user_prompt"] == "add a line to readme.md"

    @pytest.mark.asyncio
    async def test_casual_turn_never_registers_pending_action(self, monkeypatch):
        """Issue 8b — a tool-free casual reply has no tools, so a fence
        in it is not an executable proposal."""
        from services.chat_helpers import _persist_turn
        fake_db = _FakeDB()
        _patch_persist_deps(monkeypatch, fake_db)
        recorder = AsyncMock()
        monkeypatch.setattr("services.commit_boundary.propose_from_turn", recorder)

        await _persist_turn(
            "u1", "s1", "hi", "```aurem-handoff\nedit readme\n```", "deepseek",
            tier="casual",
        )

        recorder.assert_not_called()

    @pytest.mark.asyncio
    async def test_defaults_are_main_chat(self, monkeypatch):
        """No ora_panel/tier args — the old call sites keep behaving as
        main-chat turns (no tag, propose still runs)."""
        from services.chat_helpers import _persist_turn
        fake_db = _FakeDB()
        _patch_persist_deps(monkeypatch, fake_db)
        recorder = AsyncMock()
        monkeypatch.setattr("services.commit_boundary.propose_from_turn", recorder)

        await _persist_turn("u1", "s1", "hi", "hello", "deepseek")

        upd = fake_db.chat_sessions.update_calls[0][1]
        each = upd["$push"]["turns"]["$each"]
        assert "ora_panel" not in each[0] and "ora_panel" not in each[1]
        recorder.assert_called_once()


# ── commit_boundary user_prompt threading ────────────────────────────

class TestCommitBoundaryThreading:
    @pytest.mark.asyncio
    async def test_user_prompt_passed_through(self, monkeypatch):
        from services.commit_boundary import propose_from_turn
        recorder = AsyncMock()
        monkeypatch.setattr(
            "services.actions.pending_action.propose_from_turn", recorder,
        )
        await propose_from_turn(
            None, session_id="s1", user_id="u1", project_id=None,
            provider="deepseek", assistant_reply="hi",
            user_prompt="Please continue from where you left off.",
        )
        recorder.assert_called_once()
        assert recorder.call_args.kwargs["user_prompt"] == (
            "Please continue from where you left off."
        )


# ── GET /chat/history filters advisor turns ─────────────────────────

class TestChatHistoryFiltersAdvisor:
    @pytest.mark.asyncio
    async def test_advisor_turns_excluded_untagged_kept(self, monkeypatch):
        import routers.chat.history as hist_mod
        monkeypatch.setattr(
            hist_mod, "current_dev",
            AsyncMock(return_value={"user_id": "u1"}),
        )
        doc = {"title": "t", "turns": [
            {"role": "user", "content": "main q", "ts": 1},
            {"role": "assistant", "content": "main a", "ts": 2},
            {"role": "user", "content": "advisor q", "ts": 3, "ora_panel": True},
            {"role": "assistant", "content": "advisor a", "ts": 4, "ora_panel": True},
            # Old turn saved before the tag existed — keeps showing.
            {"role": "assistant", "content": "old a", "ts": 5},
        ]}
        fake_db = _FakeDB(doc=doc)
        monkeypatch.setattr(hist_mod, "get_db", lambda: fake_db)

        out = await hist_mod.chat_history("s1", authorization="Bearer <redacted>")

        contents = [m.get("content") for m in out["messages"]]
        assert contents == ["main q", "main a", "old a"]
        assert "advisor a" not in contents and "advisor q" not in contents


# ── GET /chat/sessions excludes advisor sessions ────────────────────

class TestChatSessionsListExcludesAdvisor:
    @pytest.mark.asyncio
    async def test_ora_panel_sessions_excluded_in_query(self, monkeypatch):
        import routers.chat.history as hist_mod
        monkeypatch.setattr(
            hist_mod, "current_dev",
            AsyncMock(return_value={"user_id": "u1"}),
        )
        sessions = [
            {"session_id": "main-1", "title": "hello", "updated_at": 3},
            {"session_id": "ora-panel-1730000000-abc123", "title": "advisor q", "updated_at": 2},
            {"session_id": "prod-e2e-smoke-1", "title": "smoke", "updated_at": 1},
        ]
        fake_db = _FakeDB(sessions=sessions)
        monkeypatch.setattr(hist_mod, "get_db", lambda: fake_db)

        out = await hist_mod.chat_sessions_list(authorization="Bearer <redacted>")

        # The exclusion lives in the Mongo query itself (not a
        # post-filter), so the list still returns 20 real sessions.
        assert len(fake_db.chat_sessions.find_queries) == 1
        q = fake_db.chat_sessions.find_queries[0]
        assert "$and" in q
        assert any("ora-panel" in str(clause) for clause in q["$and"])
        assert any("e2e" in str(clause) for clause in q["$and"])
        # The fake returns all rows (it can't run the regex); the
        # query assertion above is what proves the exclusion.
        assert out["ok"] is True


# ── prior-turn context skips advisor turns ──────────────────────────

def _turns_db(turns):
    return SimpleNamespace(
        chat_sessions=SimpleNamespace(
            find_one=AsyncMock(return_value={"turns": turns}),
        ),
    )


class TestPriorTurnSkipsAdvisor:
    @pytest.mark.asyncio
    async def test_context_falls_back_to_last_main_turn(self):
        from services.response_confidence import prior_turn_context_text
        db = _turns_db([
            {"role": "assistant", "content": "main answer", "ts": 1},
            {"role": "assistant", "content": "advisor answer", "ts": 2, "ora_panel": True},
        ])
        assert await prior_turn_context_text(db, "s1", "u1") == "main answer"

    @pytest.mark.asyncio
    async def test_context_none_when_only_advisor_turns(self):
        from services.response_confidence import prior_turn_context_text
        db = _turns_db([
            {"role": "assistant", "content": "advisor answer", "ts": 1, "ora_panel": True},
        ])
        assert await prior_turn_context_text(db, "s1", "u1") is None

    @pytest.mark.asyncio
    async def test_context_unchanged_without_advisor_turns(self):
        from services.response_confidence import prior_turn_context_text
        db = _turns_db([
            {"role": "assistant", "content": "main answer", "ts": 1},
        ])
        assert await prior_turn_context_text(db, "s1", "u1") == "main answer"

    @pytest.mark.asyncio
    async def test_fix_signal_ignores_advisor_turn(self):
        from services.response_confidence import prior_turn_had_fix_signal
        # Advisor turn carries a handoff fence — must NOT count.
        db = _turns_db([
            {"role": "assistant", "content": "main answer", "ts": 1},
            {"role": "assistant", "content": "```aurem-handoff\nedit README.md\n```", "ts": 2, "ora_panel": True},
        ])
        assert await prior_turn_had_fix_signal(db, "s1", "u1") is False

    @pytest.mark.asyncio
    async def test_fix_signal_still_reads_main_turn(self):
        from services.response_confidence import prior_turn_had_fix_signal
        # A fix signal is a handoff fence / "Root cause:" / task-action
        # prose — plain words like "fixed it" do NOT count.
        db = _turns_db([
            {"role": "assistant", "content": "```aurem-handoff\nedit README.md\n```", "ts": 1},
            {"role": "assistant", "content": "advisor answer", "ts": 2, "ora_panel": True},
        ])
        assert await prior_turn_had_fix_signal(db, "s1", "u1") is True
