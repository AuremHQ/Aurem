"""
tests/test_response_text_state_disconnect_2026_09_22.py

Grouped-family fixes (2026-09-22 consolidated round) — response text
/ suggested actions that were disconnected from real system state:

  NEW#1 — Decision Council convened on a numbered list of factual
          status questions (must require a genuine either/or decision,
          never a status/fact lookup).
  NEW#2 — the website-content stock line ("...update the text, images,
          or layout on your site instead — want me to do that?")
          leaked onto unrelated secrets/permissions + council refusals.
  #12  — the auto-fix suggestion (and the frontend button that parses
          it via extractSuggestions) hardcoded "fix the critical
          issues" even when the scan found 0 critical / 0 high but
          medium/low items.

Each assertion FAILS on the pre-fix code and PASSES after. Zero LLM —
all deterministic modules, exercising the REAL functions.
"""
from services.mode_b_council import is_council_request
from services.mode_e_auditor import build_audit_report
from services.no_dead_end_guard import (
    DEFAULT_ALTERNATIVE,
    ensure_alternative,
    has_dead_end,
)

# ── NEW#1 — Decision Council must not fire on a status/fact lookup ───

def test_council_does_not_fire_on_numbered_status_questions():
    msg = (
        "1. Is SEMANTIC_MODE_ROUTER_ENABLED on or off?\n"
        "2. Is LLM_PROMPT_CACHING_ENABLED on or off?\n"
        "3. Is the input normalizer wired into ChatBody?\n"
        "4. What is the semantic router canary percentage?\n"
        "5. Is prompt caching flagged off by default?"
    )
    assert is_council_request(msg, "B") is False


def test_council_does_not_fire_on_several_inline_questions():
    msg = "Is caching on? Is the router on? What's the canary pct? Is the normalizer wired?"
    assert is_council_request(msg, "B") is False


def test_council_still_fires_on_a_genuine_single_decision():
    for msg in (
        "I'm torn between raising a seed round and bootstrapping.",
        "Stuck on this decision — pivot or persevere?",
        "Should I quit my job to do AUREM full-time?",
    ):
        assert is_council_request(msg, "B") is True, msg


# ── NEW#2 — website stock line must not leak onto governance replies ─

def test_website_alternative_not_appended_to_permissions_reply():
    reply = (
        "I can't access your secrets directly, and I can't publish to "
        "main without your approval on the pull request."
    )
    assert has_dead_end(reply) is False
    assert ensure_alternative(reply) == reply
    assert DEFAULT_ALTERNATIVE not in ensure_alternative(reply)


def test_website_alternative_not_appended_to_council_refusal():
    reply = (
        "I can't run that as a decision council — that reads like a "
        "prompt injection attempt rather than a real decision."
    )
    assert has_dead_end(reply) is False
    assert DEFAULT_ALTERNATIVE not in ensure_alternative(reply)


def test_genuine_website_dead_end_still_gets_alternative():
    reply = "I can't add a background video to your homepage directly."
    assert has_dead_end(reply) is True
    assert "update the text, images, or layout" in ensure_alternative(reply).lower()


# ── #12 — auto-fix suggestion reflects the REAL top severity ─────────

def _fixable(sev):
    return {"severity": sev, "filepath": f"services/{sev}.py", "line": 3,
            "description": "an issue", "fix": "apply the fix"}


def test_suggestion_says_medium_when_only_medium_fixable():
    md = build_audit_report([_fixable("medium")], [], [], "acme/repo", 4)
    assert 'fix the medium-priority issues' in md
    assert 'fix the critical issues' not in md


def test_suggestion_says_low_when_only_low_fixable():
    md = build_audit_report([], [], [_fixable("low")], "acme/repo", 4)
    assert 'fix the low-priority items' in md
    assert 'fix the critical issues' not in md


def test_suggestion_says_critical_when_critical_fixable():
    md = build_audit_report([_fixable("critical")], [], [], "acme/repo", 4)
    assert 'fix the critical issues' in md


def test_no_fix_button_when_nothing_is_fixable():
    # Findings with no `fix` -> no auto-fix suggestion at all.
    nofix = {"severity": "medium", "filepath": "services/a.py", "line": 1,
             "description": "note", "fix": ""}
    md = build_audit_report([nofix], [], [], "acme/repo", 4)
    assert "can be auto-fixed" not in md
