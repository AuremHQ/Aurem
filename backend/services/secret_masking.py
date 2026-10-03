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
    rf"\b[A-Za-z0-9_]*{_SECRET_INDICATOR}[A-Za-z0-9_]*\s*[=:]\s*(['\"])([^'\"]{{4,}})\1",
    re.IGNORECASE,
)

# `os.environ.get("SOME_KEY", "default-value")` / `os.getenv(...)` —
# the secret-shaped indicator lives in the FIRST (key-name) argument,
# but the VALUE that needs masking is the SECOND (default) argument —
# a structurally different shape than `KEYWORD = "value"` above, so
# it needs its own pattern rather than a tweak to `_QUOTED_SECRET_RE`.
_ENV_GETTER_DEFAULT_RE = re.compile(
    rf"\b(?:os\.environ\.get|os\.getenv|config\.get)\s*\(\s*"
    rf"(['\"])([A-Za-z0-9_]*{_SECRET_INDICATOR}[A-Za-z0-9_]*)\1\s*,\s*"
    rf"(['\"])([^'\"]{{4,}})\3",
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
    `KEYWORD = "value"` / `KEYWORD: 'value'` assignment (any
    identifier ENDING in a secret keyword, e.g. `DB_PASSWORD`, not
    just a bare `password`), an `os.environ.get("KEY", "default")` /
    `os.getenv(...)` default-value argument, or a bare well-known
    vendor key shape — leaving the surrounding prose, file name,
    key NAME, and line number untouched."""
    if not text:
        return text

    def _sub_quoted(m: re.Match) -> str:
        quote, value = m.group(1), m.group(2)
        return m.group(0)[: -(len(value) + 1)] + mask_secret_value(value) + quote

    def _sub_env_default(m: re.Match) -> str:
        key_quote, key_name, val_quote, value = m.group(1), m.group(2), m.group(3), m.group(4)
        return (
            f"{m.group(0).split(key_quote, 1)[0]}"
            f"{key_quote}{key_name}{key_quote}, "
            f"{val_quote}{mask_secret_value(value)}{val_quote}"
        )

    out = _ENV_GETTER_DEFAULT_RE.sub(_sub_env_default, text)
    out = _QUOTED_SECRET_RE.sub(_sub_quoted, out)
    out = _BARE_SECRET_SHAPE_RE.sub(lambda m: mask_secret_value(m.group(0)), out)
    return out
