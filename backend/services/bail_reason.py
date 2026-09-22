"""
services/bail_reason.py — R2 (2026-08-31)

DETERMINISTIC classifier for WHY response_confidence.py's gate bailed,
replacing the useless "try rephrasing, or ask again" fallback with a
concrete next step. No LLM in this layer — the model already tried
twice (routers/chat.py's retry-once flow) and both attempts were
rejected by the gate; this is a pure heuristic over the user's OWN
words, same discipline as response_confidence.py's own deterministic
regex gates.

Every bail carries a `reason` field:
  - "missing_data"  — the request is clear, but a concrete value it
                       needs (hours, phone number, ...) is absent.
                       Ask for THAT value, plain words, one question.
  - "out_of_scope"   — the request needs something ORA structurally
                       can't do (call someone, process a payment...).
  - "low_confidence" — genuinely ambiguous; ONE specific clarifying
                       question, never "rephrase" / "tell me more".
"""
from __future__ import annotations

import re

# (keyword regex, expected-value-pattern regex, plain-language question)
_DATA_TOPICS: list[tuple[re.Pattern, re.Pattern, str]] = [
    (
        re.compile(r"\bhours?\b|\bopen\b|\bopening\b|\bclosing\b", re.IGNORECASE),
        re.compile(r"\b\d{1,2}\s*(:\d{2})?\s*(am|pm|a\.m\.|p\.m\.)\b", re.IGNORECASE),
        "What are your opening hours? Tell me the times (e.g. \"9am to 5pm\") and I'll add them.",
    ),
    (
        re.compile(r"\bphone\b|\bnumber\b|\bcall us\b", re.IGNORECASE),
        re.compile(r"\b\d[\d\-\s().]{6,}\d\b"),
        "What's the phone number? Send me the digits and I'll add it.",
    ),
    (
        re.compile(r"\baddress\b|\blocation\b|\bwhere.{0,10}located\b", re.IGNORECASE),
        re.compile(r"\b\d+\s+\w+"),
        "What's the address? Send me the street and city and I'll add it.",
    ),
    (
        re.compile(r"\bprice\b|\bcost\b|\bpricing\b", re.IGNORECASE),
        re.compile(r"[$\u20ac\u00a3]\s?\d|\b\d+\s?(dollars|usd|bucks)\b", re.IGNORECASE),
        "What's the price? Send me the number and I'll add it.",
    ),
    (
        re.compile(r"\bemail\b|\be-mail\b", re.IGNORECASE),
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
        "What's the email address? Send it over and I'll add it.",
    ),
]

_OUT_OF_SCOPE_RE = re.compile(
    r"\bcall my customers\b|\bmail (a|the) (letter|package)\b|"
    r"\bprocess (a|the) payment\b|"
    r"\bpost (this|that) on (facebook|instagram|twitter|x)\b",
    re.IGNORECASE,
)

_OUT_OF_SCOPE_MESSAGE = (
    "I can't do that directly, but I can update the text, images, or "
    "layout on your site — want me to do that instead?"
)
_LOW_CONFIDENCE_MESSAGE = (
    "Just to make sure I get this right — what exactly should change, "
    "and where on your site? (e.g. \"add our phone number to the "
    "bottom of my main page\")"
)

# 2026-09-22 — CONFIRMED live bug (founder repro, twice): a genuine,
# on-topic reply that merely CONTAINS a soft-clarifier phrase ("could
# you provide more context on which flag you mean?", "be more
# specific") was being wholesale-discarded and replaced with a canned
# bail template, and `classify_bail` then chose that template by
# keyword-matching the USER'S message — so "give me real cost/latency
# numbers" (a data-topic word "cost" present, no edit intent) came
# back as the fabricated "What's the price? Send me the number and
# I'll add it." This split separates the two very different classes:
#
#   HARD  dead-ends ("try rephrasing", "ask again", "I'm not
#         confident", "couldn't find a confident answer") — a lazy
#         non-answer regardless of length; always replaced.
#   SOFT  clarifiers ("please clarify", "be more specific", "provide
#         more detail/context/information") — a genuine, useful
#         SPECIFIC follow-up question when embedded in a substantive
#         reply; only a dead-end when it dominates a short vague reply.
_HARD_DEADEND_RE = re.compile(
    r"try\s+rephrasing|try\s+again|ask\s+again|try\s+a\s+different\s+way|"
    r"\brephrase\b|"
    r"i'?m\s+not\s+confident|i\s+am\s+not\s+confident|"
    r"could\s?n'?t\s+find\s+a\s+confident\s+answer|"
    r"could\s+not\s+find\s+a\s+confident\s+answer",
    re.IGNORECASE,
)
_SOFT_CLARIFIER_RE = re.compile(
    r"please\s+clarify|clarify\s+your\s+request|"
    r"can\s+you\s+be\s+more\s+specific|"
    r"provide\s+more\s+(detail|context|information)",
    re.IGNORECASE,
)
# A soft clarifier is treated as a banned dead-end only when the WHOLE
# reply is this short/vague. A substantive on-topic answer that also
# asks a specific clarifying question runs far longer than this.
_SOFT_CLARIFIER_MAX_WORDS = 25

# Kept for any external importer — the union of both tiers.
_BANNED_FALLBACK_RE = re.compile(
    _HARD_DEADEND_RE.pattern + "|" + _SOFT_CLARIFIER_RE.pattern,
    re.IGNORECASE,
)

# Everyday content-edit verbs a user uses when asking to PLACE a value
# on their site. A bare mention of a data-topic word (price/cost/
# phone/...) WITHOUT one of these — e.g. a long technical instruction
# that happens to say "cost/latency numbers" — must NOT be mistaken
# for "add this contact info to my site".
_CONTENT_EDIT_INTENT_RE = re.compile(
    r"\b(add|put|show|display|set|list|insert|place|include|update|"
    r"change|swap|replace|edit|write|post)\b",
    re.IGNORECASE,
)
# A contact-info request is short and task-shaped; a long instruction
# that merely contains the word "cost" is not one.
_DATA_TOPIC_MAX_WORDS = 40


def classify_bail(user_message: str) -> dict:
    """Deterministic — same input always gives the same output.
    Returns {"reason": ..., "message": <plain-language next step>}."""
    msg = user_message or ""
    if _OUT_OF_SCOPE_RE.search(msg):
        return {"reason": "out_of_scope", "message": _OUT_OF_SCOPE_MESSAGE}
    # Only treat this as a "missing contact-info value" bail when the
    # message is actually an edit-shaped request to PLACE that value —
    # not when a data-topic word merely appears inside an unrelated
    # (often long, technical) instruction. Without this gate, "give me
    # real cost/latency numbers" wrongly returned "What's the price?
    # Send me the number and I'll add it." (a fabricated task).
    if len(msg.split()) <= _DATA_TOPIC_MAX_WORDS:
        for keyword_re, value_re, question in _DATA_TOPICS:
            m = keyword_re.search(msg)
            if m and not value_re.search(msg):
                # Require a "place this value" edit verb NEAR the data
                # keyword — an incidental far-away mention ("replace the
                # LLM ... cost/latency numbers") must not be mistaken
                # for "add this contact info to my site".
                window = msg[max(0, m.start() - 40): m.end() + 40]
                if _CONTENT_EDIT_INTENT_RE.search(window):
                    return {"reason": "missing_data", "message": question}
    return {"reason": "low_confidence", "message": _LOW_CONFIDENCE_MESSAGE}


def contains_banned_fallback_phrase(text: str) -> bool:
    """2026-09-21 — Self-Repair audit Part 2.2 fix: same
    correct-refusal exemption as no_dead_end_guard.has_dead_end() —
    a reply correctly refuting a hallucinated claim or declining for a
    permission/scope reason should never be treated as a banned vague
    non-answer, even if it happens to share a word with the ban list."""
    t = text or ""
    if not (_HARD_DEADEND_RE.search(t) or _SOFT_CLARIFIER_RE.search(t)):
        return False
    from services.legitimate_refusal import is_legitimate_refusal
    if is_legitimate_refusal(t):
        return False
    # A hard dead-end is always a banned non-answer. A soft clarifier
    # is only one when it dominates a short vague reply — embedded in a
    # substantive answer it's a legitimate specific follow-up question
    # and the whole reply must NOT be discarded.
    if _HARD_DEADEND_RE.search(t):
        return True
    return len(t.split()) <= _SOFT_CLARIFIER_MAX_WORDS


def strip_banned_fallback_phrases(text: str, user_message: str) -> str:
    """Safety net for R2's copy ban: if a banned phrase ("try
    rephrasing", ...) slips into a reply from ANY path (not just the
    confidence-gate fallback this module primarily serves), replace
    the WHOLE reply with a concrete next step instead of leaving the
    useless phrase in front of the user."""
    if not contains_banned_fallback_phrase(text):
        return text
    return classify_bail(user_message)["message"]
