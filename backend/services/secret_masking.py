"""services/secret_masking.py — C3 fix (2026-10).

Founder report: ORA sometimes shows a secret's FULL raw value —
confirmed reproducible via the `save_finding` tool path
(services/local_tools.py): when a user pastes code containing a real
secret into chat and asks ORA to review it, the model's own
free-text `description`/`fix_hint` for the resulting finding can
embed the raw value verbatim, and that raw text is what gets stored
and later shown back via Fix List / the mascot / chat history.

Scope (per founder spec): mask secret values everywhere ORA shows
them — chat answers, scan results, Fix List, stored chat history
going forward — to first 2 + last 2 characters, asterisks in between.
This is a masking pass over free text, deliberately independent of
the separate rule-based scanners (bug_hunt_rules.py's `_SECRET_RULES`
etc.) which detect secrets IN REPO CODE via static messages that
never embed the raw value in the first place — this module exists
for the free-text (LLM-written) surface those rules don't cover.
"""
from __future__ import annotations

import re

# Indicator keyword immediately before a quoted value — deliberately
# conservative (named secret-shaped identifiers only) to keep false
# positives on ordinary quoted prose low.
_SECRET_INDICATOR = (
    r"(?:SECRET_KEY|secret[_-]?key|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|secret|token|password|passwd|client[_-]?secret|"
    r"auth[_-]?token|bearer)"
)
_QUOTED_SECRET_RE = re.compile(
    rf"\b{_SECRET_INDICATOR}\s*[=:]\s*(['\"])([^'\"]{{4,}})\1",
    re.IGNORECASE,
)

# Well-known vendor key SHAPES need no nearby keyword at all — the
# shape itself is the tell (same families bug_hunt_rules.py's static
# rules already recognize in code; this is the free-text equivalent).
_BARE_SECRET_SHAPE_RE = re.compile(
    r"\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|"
    r"AIza[A-Za-z0-9_\-]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
)


def mask_secret_value(raw: str) -> str:
    """first 2 + last 2 characters only, asterisks in between — the
    founder's exact spec. Short values (<=4 chars) are fully masked
    since there's nothing safe left to reveal."""
    n = len(raw)
    if n <= 4:
        return "*" * n
    return raw[:2] + "*" * (n - 4) + raw[-2:]


def redact_secrets_in_text(text: str) -> str:
    """Mask any secret-shaped value found in free text — a
    `KEYWORD = "value"` / `KEYWORD: 'value'` assignment, or a bare
    well-known vendor key shape — leaving the surrounding prose,
    file name, and line number untouched."""
    if not text:
        return text

    def _sub_quoted(m: re.Match) -> str:
        quote, value = m.group(1), m.group(2)
        full = m.group(0)
        masked = mask_secret_value(value)
        return full[: full.index(quote) + 1] + masked + quote

    out = _QUOTED_SECRET_RE.sub(_sub_quoted, text)
    out = _BARE_SECRET_SHAPE_RE.sub(lambda m: mask_secret_value(m.group(0)), out)
    return out
