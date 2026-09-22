"""
tests/test_bail_reason_task_substitution_2026_09_22.py

Regression lock for the CONFIRMED live "task substitution / invented
task" bug (founder repro, reproduced twice):

  Instance A — an explicit implementation instruction that asked for
  "real accuracy/cost/latency numbers" came back as the canned
  "What's the price? Send me the number and I'll add it." template.
  Root cause: `strip_banned_fallback_phrases` discarded the model's
  substantive reply (it merely CONTAINED a soft clarifier), then
  `classify_bail` chose a template by keyword-matching the USER'S
  message ("cost" -> price template).

  Instance B — a plain read-only status question ("confirm the status
  of three config flags") whose substantive reply contained a soft
  clarifier was replaced with the generic "what exactly should change,
  and where on your site?" template.

These tests FAIL on the pre-fix code and PASS after it. Zero LLM —
pure deterministic guards.
"""
from services.bail_reason import (
    classify_bail,
    contains_banned_fallback_phrase,
    strip_banned_fallback_phrases,
)


# ── Instance A: incidental "cost" must not fabricate a price task ────

def test_incidental_cost_word_does_not_fabricate_price_task():
    # A long, technical instruction that merely mentions "cost/latency
    # numbers" is NOT an edit-shaped "add a price to my site" request.
    prompt = (
        "Implement recommendation #4 from the audit: replace the "
        "LLM-based intent detection with Jev's structured decisions, "
        "feature-flagged off by default, and give me real accuracy, "
        "cost, and latency numbers comparing it against the current "
        "LLM path so we can decide whether to turn it on."
    )
    result = classify_bail(prompt)
    assert result["message"] != "What's the price? Send me the number and I'll add it.", (
        "an incidental 'cost' mention must not be substituted with the "
        "price contact-info template"
    )
    assert result["reason"] == "low_confidence"


def test_substantive_reply_with_soft_clarifier_is_not_discarded():
    # A real, on-topic technical answer that ends with a SPECIFIC
    # clarifying question must survive untouched — not be nuked to a
    # canned template.
    reply = (
        "Here's where those three flags stand: SEMANTIC_MODE_ROUTER_ENABLED "
        "gates the semantic router in services/semantic_mode_router.py and "
        "defaults off; LLM_PROMPT_CACHING_ENABLED gates the prompt-cache "
        "context manager and also defaults off; the input normalizer is "
        "wired as a Pydantic validator on the shared ChatBody model. If you "
        "want, I can be more specific about any one of them — just say which."
    )
    prompt = "confirm the status of those three config flags"
    assert contains_banned_fallback_phrase(reply) is False, (
        "a substantive reply that merely contains a soft clarifier must "
        "not be treated as a banned dead-end"
    )
    assert strip_banned_fallback_phrases(reply, prompt) == reply


# ── The genuine dead-ends must STILL be caught (no regression) ───────

def test_short_vague_soft_clarifier_still_banned():
    for s in ("Could you please clarify your request?", "Can you be more specific?"):
        assert contains_banned_fallback_phrase(s) is True


def test_hard_deadends_still_banned_regardless_of_length():
    long_lazy = (
        "I looked into this for a while and considered several angles but "
        "honestly I couldn't find a confident answer, so please try "
        "rephrasing your question and I'll take another look."
    )
    assert contains_banned_fallback_phrase(long_lazy) is True


def test_real_edit_shaped_data_request_still_asks_for_value():
    # The legitimate business-owner path must keep working.
    r = classify_bail("add our phone number to the footer")
    assert r["reason"] == "missing_data"
    assert "phone number" in r["message"].lower()
