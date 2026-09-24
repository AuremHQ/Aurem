"""
services/no_dead_end_guard.py — R2 addendum, Filter 14.5 (2026-08-31)

DETERMINISTIC guard: a user-facing reply that says "can't" / "cannot"
/ "unable to" must ALSO offer a concrete alternative in the same
reply ("but I can ..."). No LLM in this layer — pure regex over the
model's OWN output, same discipline as bail_reason.py /
business_voice_filter.py.

The model is told to always follow this pattern (see
BUSINESS_OWNER_VOICE_CONTRACT in routers/chat.py); this module is the
guarantee for when it forgets.
"""
from __future__ import annotations

import re

_CANT_RE = re.compile(
    r"\b(can'?t|cannot|can\s+not|unable to|not able to)\b", re.IGNORECASE,
)
_BUT_I_CAN_RE = re.compile(
    r"\bbut\s+i\s+can\b|\bhowever,?\s+i\s+can\b|\binstead,?\s+i\s+can\b"
    r"|\bi\s+can\s+(also|however|instead)\b",
    re.IGNORECASE,
)
# 2026-08-31 (Gate 1 testing_agent finding) — a reply that already
# offers a real next step ("pick your website project from the
# sidebar", "once it's connected, I'll add...") is NOT a dead end,
# even if it also contains the word "can't" earlier ("I can't read
# your pages yet"). Appending DEFAULT_ALTERNATIVE on top of an
# already-real path forward produced an awkward, redundant
# double-ask in production.
_POSITIVE_PATH_FORWARD_RE = re.compile(
    r"pick your website project|here'?s what to do|"
    r"once it'?s connected|the moment it'?s connected|"
    r"connect your (website|site|project)|i'?ll add\b",
    re.IGNORECASE,
)

# 2026-09-22 — the DEFAULT_ALTERNATIVE below is a WEBSITE-CONTENT line
# ("update the text, images, or layout"). It leaked verbatim onto
# replies that have nothing to do with editing site content — a
# secrets/permissions/approval answer and a Decision-Council refusal.
# A "can't" in one of those governance/technical/meta contexts is not
# a website-content dead end, so the website-editing alternative must
# not be appended. This only ever SUPPRESSES the append (never adds a
# wrong one), so a genuine site-content dead end is unaffected.
_NON_WEBSITE_CONTEXT_RE = re.compile(
    r"\bsecret(s)?\b|\bapi[\s\-]?keys?\b|\bcredential(s)?\b|"
    r"\bpermission(s)?\b|\bapprov(e|ed|al)\b|\bpublish(ing)?\s+to\b|"
    r"\bpush(ing)?\s+to\s+(the\s+)?main\b|\bmerge\b|"
    r"\bprompt\s+injection\b|\binjection\b|\bcouncil\b|"
    r"\benvironment\s+variable(s)?\b|\bproduction\b",
    re.IGNORECASE,
)

DEFAULT_ALTERNATIVE = (
    "but I can update the text, images, or layout on your site instead "
    "— want me to do that?"
)


def has_dead_end(text: str) -> bool:
    """True when the reply says it can't do something with no
    accompanying alternative — real ("but I can...") or a positive
    path-forward statement — anywhere in the same reply.

    2026-09-21 — Self-Repair audit Part 2.2 fix: a CORRECT, deliberate
    refusal (declining without the owner's permission, catching a
    hallucinated claim, refusing a risky out-of-scope edit) is NOT a
    dead end even if it happens to say "can't" with no "but I can" —
    see services/legitimate_refusal.py for the marker list. Only a
    genuinely stuck/vague reply should trip this guard."""
    text = text or ""
    if not _CANT_RE.search(text):
        return False
    if _BUT_I_CAN_RE.search(text) or _POSITIVE_PATH_FORWARD_RE.search(text):
        return False
    if _NON_WEBSITE_CONTEXT_RE.search(text):
        return False
    from services.legitimate_refusal import is_legitimate_refusal
    if is_legitimate_refusal(text):
        return False
    return True


def ensure_alternative(text: str, alternative: str | None = None) -> str:
    """Deterministic — appends a concrete "but I can..." alternative
    when a dead end is detected; a no-op otherwise."""
    if not text or not has_dead_end(text):
        return text
    alt = alternative or DEFAULT_ALTERNATIVE
    stripped = text.rstrip()
    if stripped.endswith((".", "!", "?")):
        # A brand-new sentence after terminal punctuation is
        # capitalised — "But I can..." not "but I can...".
        joiner, alt = " ", alt[0].upper() + alt[1:]
    else:
        joiner = " — "
    return f"{stripped}{joiner}{alt}"
