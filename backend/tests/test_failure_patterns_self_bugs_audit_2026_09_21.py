"""
tests/test_failure_patterns_self_bugs_audit_2026_09_21.py

Regression coverage for the Failure-Patterns / Self-Repair audit
(2026-09-21) — see memory/PRD.md for the full spec. Covers:

  Part 1.1 — infra vs reasoning failure split (INFRA_FAILURE_REASONS)
  Part 1.2 — broadened same-tool-no-progress loop guard
  Part 2.1 — signature() granularity fix (no more one-shared-count-
             for-every-row)
  Part 2.2 — legitimate-refusal exemption in has_dead_end() /
             contains_banned_fallback_phrase()
  Part 3   — first_seen/status/fixed_on tracking + mark_fixed()
  Part 4   — propose_fix() is propose-only, threshold-gated
"""
from __future__ import annotations

import pytest

from services.task_chain_logging import INFRA_FAILURE_REASONS
from services.orchestrator import (
    _consecutive_same_tool_no_progress, _MAX_CONSECUTIVE_SAME_TOOL_NO_PROGRESS,
)
from services.no_dead_end_guard import has_dead_end
from services.bail_reason import contains_banned_fallback_phrase
from services.legitimate_refusal import is_legitimate_refusal
from services.self_bug import signature, propose_fix, mark_fixed


# ── Part 1.1 — infra failure reasons are a real, separate category ──

def test_infra_failure_reasons_contains_provider_rate_or_5xx():
    assert "provider_rate_or_5xx" in INFRA_FAILURE_REASONS


# ── Part 1.2 — broadened tool-loop guard ─────────────────────────────

def test_same_tool_varying_args_all_failing_trips_threshold():
    invocations = [
        {"tool": "search_repo", "args": {"q": c}, "ok": False}
        for c in "abcd"
    ]
    assert _consecutive_same_tool_no_progress(invocations, "search_repo") \
        >= _MAX_CONSECUTIVE_SAME_TOOL_NO_PROGRESS


def test_a_success_resets_the_streak():
    invocations = [
        {"tool": "search_repo", "args": {"q": "a"}, "ok": False},
        {"tool": "search_repo", "args": {"q": "b"}, "ok": False},
        {"tool": "search_repo", "args": {"q": "c"}, "ok": True},
        {"tool": "search_repo", "args": {"q": "d"}, "ok": False},
    ]
    assert _consecutive_same_tool_no_progress(invocations, "search_repo") == 1


def test_different_tool_does_not_pollute_the_streak():
    invocations = [
        {"tool": "read_repo_file", "args": {}, "ok": False},
        {"tool": "search_repo", "args": {"q": "a"}, "ok": False},
    ]
    assert _consecutive_same_tool_no_progress(invocations, "search_repo") == 1
    assert _consecutive_same_tool_no_progress(invocations, "read_repo_file") == 0


# ── Part 2.1 — signature() granularity ───────────────────────────────

def test_signature_derives_distinct_subjects_from_distinct_evidence():
    sig_a = signature("dead_end_leak", {}, "I can't read your pages right now")
    sig_b = signature("dead_end_leak", {}, "no project is currently selected")
    assert sig_a != sig_b, (
        "two different dead_end_leak occurrences must NOT collapse onto "
        "the same signature — this was the exact recurrence-count bug"
    )


def test_signature_still_collapses_identical_evidence():
    sig_a = signature("dead_end_leak", {}, "Try rephrasing your question.")
    sig_b = signature("dead_end_leak", {}, "Try rephrasing your question.")
    assert sig_a == sig_b, "truly-repeated identical phrasing should still collapse"


def test_signature_backward_compatible_when_no_evidence_given():
    # Old call sites / old tests (context has no subject, no evidence
    # passed) must keep the pre-fix behaviour exactly.
    assert signature("missing_button", {}) == "missing_button:missing_button"


def test_signature_explicit_subject_still_wins_over_evidence():
    sig = signature("blank_ui", {"subject": "preview"}, "some long evidence text")
    assert sig == "blank_ui:preview"


# ── Part 2.2 — legitimate-refusal exemption ──────────────────────────

@pytest.mark.parametrize("text", [
    "I can't clone that site without the owner's permission — want me to do something else instead?",
    "Actually, that claim was incorrect — no such exec() call exists in this file, let me correct that.",
    "I can't apply that edit because it would touch unrelated code outside the scope of this change.",
])
def test_legitimate_refusals_are_not_dead_ends(text):
    assert is_legitimate_refusal(text)
    assert has_dead_end(text) is False


def test_genuinely_stuck_reply_still_flagged():
    stuck = "I can't figure this out. Try rephrasing your question."
    assert has_dead_end(stuck) is True
    assert contains_banned_fallback_phrase(stuck) is True


def test_banned_phrase_exempted_when_it_is_a_correct_refusal():
    # Contains a banned-list word ("clarify") but is a real, correct
    # scope-refusal, not a vague non-answer.
    text = ("I won't make that change — please clarify one thing first: "
            "it would touch unrelated code outside the scope of this fix, "
            "so I want to confirm before touching it.")
    assert contains_banned_fallback_phrase(text) is False


# ── Part 3/4 — first_seen/status/mark_fixed/propose_fix (fake DB) ────

class _FakeLearnedCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def find_one_and_update(self, query, update, upsert=True, return_document=None):
        sig = query["signature"]
        doc = self.docs.get(sig)
        if doc is None:
            doc = {"signature": sig, "times_seen": 0}
            doc.update(update.get("$setOnInsert", {}))
        doc["times_seen"] += update["$inc"]["times_seen"]
        doc.update(update["$set"])
        self.docs[sig] = doc
        return dict(doc)

    async def update_one(self, query, update):
        sig = query["signature"]
        existed = sig in self.docs

        class _Result:
            matched_count = 1 if existed else 0
        if existed:
            self.docs[sig].update(update.get("$set", {}))
        return _Result()

    async def find_one(self, query, *a, **kw):
        return self.docs.get(query.get("signature"))


class _FakeSelfBugsCollection:
    def __init__(self):
        self.inserts = []

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))


class _FakeDB:
    def __init__(self):
        self.ora_self_bugs = _FakeSelfBugsCollection()
        self.self_bug_learned = _FakeLearnedCollection()


def test_propose_fix_is_none_below_threshold():
    assert propose_fix("dead_end_leak", 4, "open") is None


def test_propose_fix_returns_template_at_threshold_when_open():
    text = propose_fix("dead_end_leak", 5, "open")
    assert text and "dead end" in text.lower()


def test_propose_fix_none_when_status_is_fixed():
    assert propose_fix("dead_end_leak", 50, "fixed") is None


@pytest.mark.asyncio
async def test_emit_writes_first_seen_status_and_proposal_at_threshold(monkeypatch):
    import services.self_bug as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)

    for _ in range(5):
        await m.emit("dead_end_leak", "same evidence every time", {}, source="no_dead_end_guard")

    sig = m.signature("dead_end_leak", {}, "same evidence every time")
    learned = fake_db.self_bug_learned.docs[sig]
    assert learned["times_seen"] == 5
    assert learned["status"] == "open"
    assert learned["first_seen"] is not None
    # 5th occurrence crosses _PROPOSED_FIX_RECURRENCE_THRESHOLD (5) -> proposal logged
    assert learned["proposed_fix_text"]
    assert learned["proposed_fix_review_status"] == "pending"


@pytest.mark.asyncio
async def test_mark_fixed_updates_status_and_returns_false_if_missing(monkeypatch):
    import services.self_bug as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)

    await m.emit("blank_ui", "e", {"subject": "preview"}, source="ui")
    sig = m.signature("blank_ui", {"subject": "preview"}, "e")

    assert await mark_fixed(sig, "fixed", fixed_by="admin@x.com") is True
    row = fake_db.self_bug_learned.docs[sig]
    assert row["status"] == "fixed"
    assert row["fixed_by"] == "admin@x.com"
    assert row["fixed_on"] is not None

    assert await mark_fixed("no-such-signature", "fixed") is False


@pytest.mark.asyncio
async def test_mark_fixed_rejects_bad_status(monkeypatch):
    import services.self_bug as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)
    with pytest.raises(ValueError):
        await mark_fixed("some:sig", "bogus")


# ── Part 4 — no auto-apply mechanism exists anywhere ─────────────────

def test_no_auto_apply_mechanism_exists():
    """propose_fix()/emit() only ever WRITE text fields for a human to
    read. Guard against a future edit accidentally wiring this into
    an auto-apply path — same discipline as the existing P7-C
    self-modify guard test."""
    src = open("services/self_bug.py").read()
    for banned in ("subprocess", "os.system", "git.", "write_repo_file",
                   "commit_and_push", "apply_patch", "auto_apply"):
        assert banned not in src, f"self_bug.py must stay propose-only, found: {banned!r}"
