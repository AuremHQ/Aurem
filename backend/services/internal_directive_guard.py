"""
services/internal_directive_guard.py — 2026-02

DETERMINISTIC user-facing reply filter, same discipline as
business_voice_filter.py / citation_guard.py / response_confidence.py:
NO LLM in this layer.

Confirmed live bug (founder QA repro, exec_usage/injection_scanner.py
lookup against a project where the file didn't exist at that path):
`services/local_tools.py`'s `read_repo_file` 404 error deliberately
returns a LOUD, directive-style tool-result string ("STOP guessing
paths. Your next tool call MUST be `list_repo_files`...") — that text
is meant ONLY to steer the model's OWN next tool call (Iter 37's
original intent: stop it from silently fabricating an analysis of a
path that doesn't exist). It is never meant to reach the end user.
Usually the model reads it, calls `list_repo_files` as instructed, and
writes a clean final reply — but confirmed live, it sometimes instead
pastes that raw tool-result text straight into its final user-facing
response, verbatim, self-directed imperatives and all ("STOP guessing
paths... do not cite any file paths...").

This is the SAME class of problem business_voice_filter.py already
solves for dev jargon — the model can't be trusted to always keep an
internal instruction internal, so the boundary has to be enforced
deterministically on the way out, not hoped for on the way in.

Scope: only removes sentences that are PURELY self-directed (no
information content for the end user). The sibling GitHub-access-
revoked error in local_tools.py deliberately embeds a real end-user
message ("Tell the user clearly: 'GitHub access to X was revoked —
reconnect...'") inside the same tool-result string.

Part XII (d3) — DESIGN CONFLICT, recorded here deliberately: this
docstring used to say that quoted sentence was "left untouched" on
purpose, because its CONTENT is real end-user information. That is
still true of the content — but the MODEL-DIRECTED FRAMING around
it ("Tell the user clearly:", plus the leading "OAuth/PAT token
expired." tool-error fragment) is not user content, and it was
leaking verbatim into user-facing replies. The fix unwraps the
framing and keeps the message: the prefix (and its surrounding
quotes) are stripped, the revoked-access message itself survives.
A cleaner long-term fix is to restructure
`_github_access_revoked_error` in local_tools.py to carry a separate
`user_message` field so this guard never has to parse model-directed
prose at all — NOT done here (local_tools.py is out of this lane's
scope); the unwrap below is the guard-side part.
"""
from __future__ import annotations

import re

_INTERNAL_DIRECTIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # local_tools.py read_repo_file's 404 error — the whole model-only
    # instruction block, from "STOP guessing paths" through to the end
    # of that guidance sentence.
    (re.compile(
        r"STOP guessing paths\.\s*Your next tool call MUST be.*?"
        r"(?:actual layout\.|training data\.)\s*",
        re.IGNORECASE | re.DOTALL,
    ), ""),
    # local_tools.py's GitHub-access-revoked error — only the two
    # purely-internal sentences in that template.
    (re.compile(
        r"STOP calling repo tools — they will all fail the same way "
        r"until access is restored\.\s*",
        re.IGNORECASE,
    ), ""),
    (re.compile(
        r"Do not guess at file paths or blame an unrelated API\.\s*",
        re.IGNORECASE,
    ), ""),
    # Part XII (d3) — unwrap the model-directed framing around the
    # revoked-access message. First pattern: the quoted form
    # `Tell the user clearly: '...message...'` → just the message
    # (prefix AND surrounding quotes removed, message kept via \1).
    # The closing quote must follow sentence punctuation so a message
    # containing an apostrophe ("Can't ...") can never be truncated
    # mid-word — if that pattern doesn't match, the generic prefix
    # strip below still removes "Tell the user clearly: " and leaves
    # the quotes.
    (re.compile(
        r"Tell the user clearly:\s*'([^']*[.!?])'\s*",
        re.IGNORECASE,
    ), r"\1"),
    # Generic fallback: strip the directive prefix whatever follows
    # it (double-quoted, unquoted, or a message the first pattern
    # could not unwrap safely).
    (re.compile(
        r"Tell the user clearly:\s*",
        re.IGNORECASE,
    ), ""),
    # The leading tool-error fragment from the same template
    # ("OAuth/PAT token expired. ...") — tool-result text, not
    # user-facing content.
    (re.compile(
        r"OAuth/PAT token expired\.\s*",
        re.IGNORECASE,
    ), ""),
]


def strip_leaked_tool_directives(text: str) -> str:
    """Deterministic — same input always produces the same output, no
    LLM call. Removes known self-directed tool-result instruction
    text if the model pasted it verbatim into a final user-facing
    reply. Returns `text` unchanged when none of the patterns match."""
    if not text:
        return text
    out = text
    for pat, replacement in _INTERNAL_DIRECTIVE_PATTERNS:
        out = pat.sub(replacement, out)
    return out
