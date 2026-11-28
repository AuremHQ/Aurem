"""2026-11-27 — chat continue / confirm fixes (Issues 2, 3, 4, 8).

The bugs, all from live session 6526a5b3:
  Issue 2 — a turn that announced "Let me / I'll / Checking..." was
    wrapped as an explicit partial ("I paused partway through..."),
    and the "Continue" button sent "Please continue from where you
    left off." — which classified as CASUAL (no file-edit verb, no
    resource noun) and announced again. A tool-free loop.
  Issue 3 — a vague proposal ("add that line on homepage", no file
    path) extracted no concrete (path, old, new) triple, so nothing
    was stored as pending; the next "yes" found nothing and answered
    "I don't have a change waiting right now". Worse, the next
    unrelated turn auto-cancelled anything that WAS pending.
  Issue 4 — a bare "yes" after an offer ("Would you like me to...?")
    matched no stored pending action, so it restarted as a fresh
    casual turn ("I have not checked this yet").

The fixes:
  - `has_file_edit_intent` (core/intent_gateway.py): a message naming
    a file AND an edit verb is not casual — it falls through to the
    tool pipeline (chat_pre_llm step 5 guard).
  - `extract_offer_task` / `offer_confirmation_task`
    (services/response_confidence.py): a bare confirmation (or the
    Continue phrase) right after an offer resolves against that
    offer; resolve_pre_llm overrides the tier to agentic so the
    confirmed task runs with tools.
  - `propose_from_turn` (services/actions/pending_action.py): a fenced
    proposal with no concrete triple is stored as a PROPOSED
    "needs-detail" record (never executable, never AWAITING_CONFIRM);
    the auto-cancel skips continuation/confirmation prompts; a
    confirm with only a needs-detail record asks for the missing
    file instead of claiming nothing is waiting; cancel drops
    needs-detail records too.

Pure unit tests — fake in-memory pending_actions collection, no live
Mongo, no network. The resolve_pre_llm tests mirror the fixtures in
test_chat_pre_llm_dedup_2026_09_06.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

USER = {"user_id": "u1", "email": "user@example.com", "tier": "free"}


class _Body:
    """Minimal stand-in for the chat_send/chat_stream Pydantic body."""
    def __init__(self, prompt, session_id="s1", project_id=None, ora_panel=False):
        self.prompt = prompt
        self.session_id = session_id
        self.project_id = project_id
        self.ora_panel = ora_panel


# ── has_file_edit_intent (Issue 2) ───────────────────────────────────

class TestHasFileEditIntent:
    @pytest.mark.parametrize("msg", [
        "in readme.md add this line",
        "add one line to readme.md",
        "update services/llm.py and fix the bug",
        "please edit README.md",
    ])
    def test_file_plus_edit_verb_is_intent(self, msg):
        from core.intent_gateway import has_file_edit_intent
        assert has_file_edit_intent(msg) is True

    @pytest.mark.parametrize("msg", [
        "what does readme.md say?",
        "add that line on homepage",  # no file reference
        "hello there",
        "read the docs please",
    ])
    def test_no_intent_without_file_or_verb(self, msg):
        from core.intent_gateway import has_file_edit_intent
        assert has_file_edit_intent(msg) is False


# ── offer extraction (Issue 4) ──────────────────────────────────────

class TestOfferExtraction:
    @pytest.mark.parametrize("prior,expected", [
        ("Would you like me to help you find the company's official contact page?",
         "find the company's official contact page"),
        ("Shall I check the pricing page for you?", "check the pricing page for you"),
        ("Want me to continue?", "continue"),
        ("Should I update the docs now?", "update the docs now"),
        ("Here is the summary of the repo. Let me know if you need anything.", None),
        ("Would you like me to fix it? I already fixed the typo yesterday.", "fix it"),
    ])
    def test_extract_offer_task(self, prior, expected):
        from services.response_confidence import extract_offer_task
        assert extract_offer_task(prior) == expected

    def test_offer_task_empty_prior(self):
        from services.response_confidence import extract_offer_task
        assert extract_offer_task("") is None
        assert extract_offer_task(None) is None

    @pytest.mark.parametrize("msg,prior,expected", [
        ("yes", "Would you like me to help you find the company's official contact page?",
         "find the company's official contact page"),
        ("Please continue from where you left off.",
         "That was my next planned step, not a finished result. Want me to continue?",
         "continue"),
        ("yes", "Here is the summary.", None),
        ("what is the status?", "Would you like me to fix it?", None),
        ("yes please", "Shall I check the pricing page?", "check the pricing page"),
    ])
    def test_offer_confirmation_task(self, msg, prior, expected):
        from services.response_confidence import offer_confirmation_task
        assert offer_confirmation_task(msg, prior) == expected

    def test_is_continue_prompt(self):
        from services.response_confidence import is_continue_prompt
        assert is_continue_prompt("Please continue from where you left off.") is True
        assert is_continue_prompt("please continue") is False


# ── resolve_pre_llm: offer + continue + file-edit (Issues 2, 4) ─────

async def _classify_casual(*a, **kw):
    return {"tier": "casual", "confidence": 0.9}


async def _quota_ok(*a, **kw):
    return None


class TestResolvePreLlmOfferAndContinue:
    @pytest.mark.asyncio
    async def test_yes_after_offer_runs_with_tools(self, monkeypatch):
        """Issue 4 — 'yes' after an offer is an action, not chit-chat:
        tier overrides to agentic and nothing short-circuits."""
        from routers.chat_pre_llm import resolve_pre_llm
        monkeypatch.setattr("core.intent_gateway.classify", _classify_casual)
        monkeypatch.setattr("services.usage.assert_has_task_budget", _quota_ok)
        outcome = await resolve_pre_llm(
            db=None, user=USER, body=_Body("yes"), bin_ctx=None,
            prior_fix_signal=False,
            prior_turn_text="Would you like me to help you find the company's official contact page?",
            session_summary="", allowed_modes=["swift"], req_mode="swift",
            run_confirm_boundary=False,
        )
        assert outcome.result is None, "a confirmed offer must fall through to the tool pipeline"
        assert outcome.tier == "agentic"
        assert "offer_confirmation_override" in outcome.intent_result.get("signals", [])

    @pytest.mark.asyncio
    async def test_yes_after_offer_with_confirm_boundary_on(self, monkeypatch):
        """Issue 4, production path — with the confirm boundary ON
        (the default), step 1 answers 'yes' with 'I don't have a
        change waiting' when nothing is pending. That result must be
        DISCARDED when the prior turn offered a task, so the offer
        override can route the turn to the tools."""
        from routers.chat_pre_llm import resolve_pre_llm
        monkeypatch.setattr("core.intent_gateway.classify", _classify_casual)
        monkeypatch.setattr("services.usage.assert_has_task_budget", _quota_ok)
        monkeypatch.setattr(
            "services.commit_boundary.commit_boundary_enabled", lambda: True,
        )
        outcome = await resolve_pre_llm(
            db=_FakeDB2(), user=USER, body=_Body("yes"), bin_ctx=None,
            prior_fix_signal=False,
            prior_turn_text="Would you like me to help you find the company's official contact page?",
            session_summary="", allowed_modes=["swift"], req_mode="swift",
            run_confirm_boundary=True,
        )
        assert outcome.result is None, (
            "step 1's no-pending reply must not stand when the 'yes' "
            "was answering an offer"
        )
        assert outcome.tier == "agentic"

    @pytest.mark.asyncio
    async def test_yes_without_offer_with_confirm_boundary_on_keeps_no_pending(self, monkeypatch):
        """Same production path, no offer — step 1's honest
        no-pending reply is KEPT."""
        from routers.chat_pre_llm import resolve_pre_llm
        monkeypatch.setattr("core.intent_gateway.classify", _classify_casual)
        monkeypatch.setattr(
            "services.commit_boundary.commit_boundary_enabled", lambda: True,
        )
        outcome = await resolve_pre_llm(
            db=_FakeDB2(), user=USER, body=_Body("yes"), bin_ctx=None,
            prior_fix_signal=False, prior_turn_text="Here is the summary.",
            session_summary="", allowed_modes=["swift"], req_mode="swift",
            run_confirm_boundary=True,
        )
        r = outcome.result
        assert r is not None
        assert r["provider"] == "commit-boundary-no-pending"

    @pytest.mark.asyncio
    async def test_yes_without_offer_keeps_old_canned_reply(self, monkeypatch):
        """No offer in the prior turn — the old 'nothing pending'
        short-circuit is preserved exactly."""
        from routers.chat_pre_llm import resolve_pre_llm
        monkeypatch.setattr("core.intent_gateway.classify", _classify_casual)
        outcome = await resolve_pre_llm(
            db=None, user=USER, body=_Body("yes"), bin_ctx=None,
            prior_fix_signal=False, prior_turn_text="Here is the summary.",
            session_summary="", allowed_modes=["swift"], req_mode="swift",
            run_confirm_boundary=False,
        )
        r = outcome.result
        assert r is not None
        assert r["provider"] == "intent-gateway-no-pending-fix"

    @pytest.mark.asyncio
    async def test_continue_after_partial_runs_with_tools(self, monkeypatch):
        """Issue 2 — 'Continue' after a partial turn runs with tools
        instead of looping through the tool-free casual path."""
        from routers.chat_pre_llm import resolve_pre_llm
        monkeypatch.setattr("core.intent_gateway.classify", _classify_casual)
        monkeypatch.setattr("services.usage.assert_has_task_budget", _quota_ok)
        outcome = await resolve_pre_llm(
            db=None, user=USER,
            body=_Body("Please continue from where you left off."), bin_ctx=None,
            prior_fix_signal=False,
            prior_turn_text="I paused partway through — that was my next planned step, not a finished result. Want me to continue?",
            session_summary="", allowed_modes=["swift"], req_mode="swift",
            run_confirm_boundary=False,
        )
        assert outcome.result is None
        assert outcome.tier == "agentic"

    @pytest.mark.asyncio
    async def test_continue_without_partial_stays_casual(self, monkeypatch):
        """The Continue phrase with NO partial marker in the prior turn
        is still an ordinary casual reply — the override is narrow."""
        from routers.chat_pre_llm import resolve_pre_llm
        monkeypatch.setattr("core.intent_gateway.classify", _classify_casual)
        monkeypatch.setattr(
            "services.intent_gateway_casual_reply.casual_direct_reply",
            AsyncMock(return_value="Sure, continuing."),
        )
        monkeypatch.setattr(
            "services.business_voice_filter.apply_business_owner_guards",
            AsyncMock(side_effect=lambda ora_panel, text, *a, **kw: text),
        )
        outcome = await resolve_pre_llm(
            db=None, user=USER,
            body=_Body("Please continue from where you left off."), bin_ctx=None,
            prior_fix_signal=False, prior_turn_text="Here is the summary.",
            session_summary="", allowed_modes=["swift"], req_mode="swift",
            run_confirm_boundary=False,
        )
        r = outcome.result
        assert r is not None
        assert r["provider"] == "intent-gateway-casual"

    @pytest.mark.asyncio
    async def test_file_edit_request_skips_casual_reply(self, monkeypatch):
        """Issue 2 — 'add this line to readme.md' names a file, so it
        must not be answered by the tool-free casual path."""
        from routers.chat_pre_llm import resolve_pre_llm
        monkeypatch.setattr("core.intent_gateway.classify", _classify_casual)
        monkeypatch.setattr("services.usage.assert_has_task_budget", _quota_ok)
        outcome = await resolve_pre_llm(
            db=None, user=USER, body=_Body("in readme.md add this line"), bin_ctx=None,
            prior_fix_signal=False, prior_turn_text="",
            session_summary="", allowed_modes=["swift"], req_mode="swift",
            run_confirm_boundary=False,
        )
        assert outcome.result is None, "a file edit must fall through to chat_with_tools"
        assert outcome.tier == "casual"  # tier unchanged — only the short-circuit is skipped

    @pytest.mark.asyncio
    async def test_vague_edit_without_file_stays_casual(self, monkeypatch):
        """No file named ('add that line on homepage') — the guard does
        not fire; the casual path answers as before."""
        from routers.chat_pre_llm import resolve_pre_llm
        monkeypatch.setattr("core.intent_gateway.classify", _classify_casual)
        monkeypatch.setattr(
            "services.intent_gateway_casual_reply.casual_direct_reply",
            AsyncMock(return_value="Which file should it go in?"),
        )
        monkeypatch.setattr(
            "services.business_voice_filter.apply_business_owner_guards",
            AsyncMock(side_effect=lambda ora_panel, text, *a, **kw: text),
        )
        outcome = await resolve_pre_llm(
            db=None, user=USER, body=_Body("add that line on homepage"), bin_ctx=None,
            prior_fix_signal=False, prior_turn_text="",
            session_summary="", allowed_modes=["swift"], req_mode="swift",
            run_confirm_boundary=False,
        )
        r = outcome.result
        assert r is not None
        assert r["provider"] == "intent-gateway-casual"


# ── pending_action needs-detail (Issue 3) ───────────────────────────

class _FakePendingActions:
    """In-memory stand-in for db.pending_actions, supporting exactly
    the queries pending_action.py issues."""
    def __init__(self):
        self.docs = []

    def _match(self, doc, q):
        for k, v in q.items():
            if isinstance(v, dict) and "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            elif isinstance(v, dict) and "$gte" in v:
                if (doc.get(k) or 0) < v["$gte"]:
                    return False
            elif doc.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc["id"])

    def find(self, q, *a, **kw):
        return _FakeFindCursor([d for d in self.docs if self._match(d, q)])

    async def find_one(self, q, *a, **kw):
        for d in self.docs:
            if self._match(d, q):
                return d
        return None

    async def update_one(self, filt, upd, *a, **kw):
        for d in self.docs:
            if self._match(d, filt):
                d.update(upd.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, filt, upd, *a, **kw):
        n = 0
        for d in self.docs:
            if self._match(d, filt):
                d.update(upd.get("$set", {}))
                n += 1
        return SimpleNamespace(matched_count=n, modified_count=n)


class _FakeFindCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return self._docs[:length] if length else list(self._docs)


class _FakeDB2:
    def __init__(self):
        self.pending_actions = _FakePendingActions()


_VAGUE_FENCE = "```aurem-handoff\nadd that line on homepage\n```"


def _seed_awaiting(fake_db):
    fake_db.pending_actions.docs.append({
        "id": "act-1", "session_id": "s1", "user_id": "u1",
        "status": "AWAITING_CONFIRM", "type": "edit",
        "payload": {"path": "README.md", "old_value": "a", "new_value": "b"},
        "created_at": 9999999999.0, "updated_at": 9999999999.0,
    })


class TestNeedsDetailRecord:
    @pytest.mark.asyncio
    async def test_vague_fence_stored_as_needs_detail(self):
        """Issue 3 — a proposal with no concrete triple is stored as a
        PROPOSED needs-detail record, never as an executable action."""
        from services.actions.pending_action import propose_from_turn
        fake_db = _FakeDB2()
        await propose_from_turn(
            fake_db, session_id="s1", user_id="u1", project_id=None,
            provider="deepseek", assistant_reply=_VAGUE_FENCE,
            user_prompt="add that line on homepage",
        )
        docs = fake_db.pending_actions.docs
        assert len(docs) == 1
        doc = docs[0]
        assert doc["status"] == "PROPOSED"
        assert doc.get("needs_detail") is True
        assert doc["payload"]["brief"]
        assert "homepage" in doc["payload"]["brief"]

    @pytest.mark.asyncio
    async def test_confirm_with_only_needs_detail_asks_for_file(self):
        """Issue 3 — 'yes' after a vague proposal must ask for the
        missing file, never claim 'I don't have a change waiting'."""
        from services.actions.pending_action import (
            propose_from_turn, resolve_confirm, PROVIDER_NEEDS_DETAIL,
            NO_PENDING_ACTIONABLE_MESSAGE,
        )
        fake_db = _FakeDB2()
        await propose_from_turn(
            fake_db, session_id="s1", user_id="u1", project_id=None,
            provider="deepseek", assistant_reply=_VAGUE_FENCE,
            user_prompt="add that line on homepage",
        )
        out = await resolve_confirm(
            fake_db, session_id="s1", user_id="u1", project_id=None,
            prompt="yes", user={}, bin_ctx=None,
        )
        assert out is not None
        assert out["provider"] == PROVIDER_NEEDS_DETAIL
        assert "which file" in out["content"].lower()
        assert out["content"] != NO_PENDING_ACTIONABLE_MESSAGE

    @pytest.mark.asyncio
    async def test_confirm_with_nothing_pending_keeps_old_message(self):
        """No proposal at all — the old honest message is preserved."""
        from services.actions.pending_action import (
            resolve_confirm, NO_PENDING_ACTIONABLE_MESSAGE,
        )
        fake_db = _FakeDB2()
        out = await resolve_confirm(
            fake_db, session_id="s1", user_id="u1", project_id=None,
            prompt="yes", user={}, bin_ctx=None,
        )
        assert out is not None
        assert out["content"] == NO_PENDING_ACTIONABLE_MESSAGE

    @pytest.mark.asyncio
    async def test_cancel_drops_needs_detail_record(self):
        from services.actions.pending_action import (
            propose_from_turn, resolve_confirm, STATUS_CANCELLED,
        )
        fake_db = _FakeDB2()
        await propose_from_turn(
            fake_db, session_id="s1", user_id="u1", project_id=None,
            provider="deepseek", assistant_reply=_VAGUE_FENCE,
            user_prompt="add that line on homepage",
        )
        out = await resolve_confirm(
            fake_db, session_id="s1", user_id="u1", project_id=None,
            prompt="cancel that", user={}, bin_ctx=None,
        )
        assert out is not None and "Cancelled" in out["content"]
        assert fake_db.pending_actions.docs[0]["status"] == STATUS_CANCELLED

    @pytest.mark.asyncio
    async def test_auto_cancel_skipped_for_continue_prompt(self):
        """Issue 3 — 'Please continue' must not wipe a pending action."""
        from services.actions.pending_action import propose_from_turn, STATUS_AWAITING_CONFIRM
        fake_db = _FakeDB2()
        _seed_awaiting(fake_db)
        await propose_from_turn(
            fake_db, session_id="s1", user_id="u1", project_id=None,
            provider="deepseek", assistant_reply="here is the file contents",
            user_prompt="Please continue from where you left off.",
        )
        assert fake_db.pending_actions.docs[0]["status"] == STATUS_AWAITING_CONFIRM

    @pytest.mark.asyncio
    async def test_auto_cancel_skipped_for_yes(self):
        from services.actions.pending_action import propose_from_turn, STATUS_AWAITING_CONFIRM
        fake_db = _FakeDB2()
        _seed_awaiting(fake_db)
        await propose_from_turn(
            fake_db, session_id="s1", user_id="u1", project_id=None,
            provider="deepseek", assistant_reply="anything else?",
            user_prompt="yes",
        )
        assert fake_db.pending_actions.docs[0]["status"] == STATUS_AWAITING_CONFIRM

    @pytest.mark.asyncio
    async def test_auto_cancel_still_fires_for_unrelated_turn(self):
        """The old guard still works: a genuinely new, unrelated turn
        cancels a stale pending action."""
        from services.actions.pending_action import propose_from_turn, STATUS_CANCELLED
        fake_db = _FakeDB2()
        _seed_awaiting(fake_db)
        await propose_from_turn(
            fake_db, session_id="s1", user_id="u1", project_id=None,
            provider="deepseek", assistant_reply="sunny today",
            user_prompt="what is the weather?",
        )
        assert fake_db.pending_actions.docs[0]["status"] == STATUS_CANCELLED
