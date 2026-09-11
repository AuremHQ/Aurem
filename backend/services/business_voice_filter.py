"""
services/business_voice_filter.py — R1 (2026-08-31)

DETERMINISTIC user-facing reply filter, same discipline as
hallucination_guard.py / citation_guard.py / response_confidence.py:
NO LLM in this layer. The model proposes a reply; this filter
guarantees a non-technical business owner never sees a raw filename
(with extension) or a developer term in what they read.

Scope: ONLY the string returned/streamed to the USER. Tool-calls (e.g.
`read_repo_file`, `write_repo_file`) keep the REAL file path exactly as
the model chose it — this filter never touches `args`/tool-call
payloads, only final user-facing `content`. Breaking that separation
would break ORA's ability to target the right file.

Callers: routers/chat.py — applied at every point `content` is about
to be returned to the user, gated on `not body.ora_panel` (the ORA
Admin/technical view intentionally keeps real filenames — see
ORA_PANEL_TONE in chat.py).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from functools import lru_cache

_MAP_PATH = Path(__file__).resolve().parent.parent / "config" / "business_voice_map.json"

# Same file-path signal response_confidence.py uses for its own
# _FILE_PATH_RE, kept in sync deliberately (both must recognize the
# same universe of "this looks like a filename").
_FILENAME_RE = re.compile(
    r"\b[\w\-./]+\.(?:py|jsx?|tsx?|json|md|ya?ml|css|s?css|html?|txt|sql|env|sh)\b",
    re.IGNORECASE,
)

_PAGE_NAME_HINTS = (
    (re.compile(r"index|home|landing", re.IGNORECASE), "your homepage"),
    (re.compile(r"about", re.IGNORECASE), "your About page"),
    (re.compile(r"contact", re.IGNORECASE), "your Contact page"),
    (re.compile(r"footer", re.IGNORECASE), "the bottom of your page"),
    (re.compile(r"header|nav", re.IGNORECASE), "the top of your page"),
    (re.compile(r"pricing|price", re.IGNORECASE), "your pricing page"),
)
_DEFAULT_PAGE_TERM = "your page"

# 2026-09-12 — confirmed live bug (founder repro: "add a comment to
# the top of package.json"): _FILENAME_RE also matches non-page
# extensions (json/py/yml/sql/env/sh/txt/md) — real backend/config
# files that have NOTHING to do with a "page". None of the
# _PAGE_NAME_HINTS patterns above can ever match those (there's no
# "home"/"about"/"footer" concept in a config file), so every one of
# them silently fell through to the generic _DEFAULT_PAGE_TERM,
# producing an incoherent "your page" in place of a real filename
# reference the user themselves had just typed (e.g. "package.json"
# -> "your page" x3 in one reply). Only extensions a business owner
# would actually think of as a visual "page" get the page-flavored
# default; everything else gets an accurate, still-jargon-free
# fallback instead of a nonsensical one.
_PAGE_LIKE_EXTS = {"html", "htm", "jsx", "tsx", "css", "scss"}
_EXT_RE = re.compile(r"\.([A-Za-z0-9]+)$")
_DEFAULT_FILE_TERM = "that file"


@lru_cache(maxsize=1)
def _load_map() -> dict[str, str]:
    raw = json.loads(_MAP_PATH.read_text())
    return {k.lower(): v for k, v in raw.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def _combined_pattern() -> re.Pattern:
    # 2026-09-03 (P2 user-first wave) — a SINGLE combined-alternation
    # pass, longest term first, so e.g. "github repo" consumes both
    # words in one match and the standalone "github"/"repo" entries
    # never get a second, cascading crack at the same text. Applying
    # each entry as its OWN separate .sub() (the original design) let
    # one substitution's OUTPUT text collide with a LATER entry's
    # pattern (e.g. "github repo" -> "...code" -> "code" itself is
    # also a map key -> mangled into "...content"). A single pass over
    # the ORIGINAL text makes that class of bug structurally
    # impossible, for this map and any future entries added to it.
    terms = sorted(_load_map().keys(), key=len, reverse=True)
    alternation = "|".join(re.escape(t) for t in terms)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


def _replace_term(match: re.Match) -> str:
    replacement = _load_map().get(match.group(0).lower())
    if replacement is None:
        return match.group(0)
    return _preserve_case(match.group(0), replacement)


def _humanize_filename(match: re.Match) -> str:
    stem = match.group(0)
    for pattern, label in _PAGE_NAME_HINTS:
        if pattern.search(stem):
            return label
    ext_match = _EXT_RE.search(stem)
    ext = ext_match.group(1).lower() if ext_match else ""
    if ext in _PAGE_LIKE_EXTS:
        return _DEFAULT_PAGE_TERM
    return _DEFAULT_FILE_TERM


def _preserve_case(original: str, replacement: str) -> str:
    if original[:1].isupper() and not replacement[:1].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


_AN_FIX_RE = re.compile(r"\b(a)\s+(?=[aeiouAEIOU])", re.IGNORECASE)

# 2026-08-31 (Gate 1 T4 testing_agent finding) — "I don't have access
# to X" is banned dev/system framing regardless of whether the reply
# is a design ask (design_refusal_guard.py only fires on design asks;
# this phrasing shows up on ANY "your site isn't connected yet" reply,
# so it needs to be caught here, universally).
_ACCESS_TO_RE = re.compile(
    r"i (don'?t|do not|didn'?t) (currently )?have access to\b",
    re.IGNORECASE,
)


def _fix_a_an(text: str) -> str:
    """Cosmetic-only pass after term substitution: 'a update' -> 'an
    update'. Never touches correctness, only grammar of the rewritten
    business-owner-facing sentence."""
    def _repl(m: re.Match) -> str:
        return ("An " if m.group(1)[0].isupper() else "an ")
    return _AN_FIX_RE.sub(_repl, text)


def filter_for_business_owner(text: str) -> str:
    """Deterministic — same input always produces the same output, no
    LLM call. Strips filenames-with-extension (replaced with a plain
    page reference) and rewrites dev terms to business-owner terms."""
    if not text:
        return text
    out = _FILENAME_RE.sub(_humanize_filename, text)
    out = _ACCESS_TO_RE.sub(lambda m: _preserve_case(m.group(0), "I can't see"), out)
    out = _combined_pattern().sub(_replace_term, out)
    return _fix_a_an(out)


def apply_business_voice(ora_panel: bool, content: str) -> str:
    """Convenience wrapper for routers/chat.py's call sites — the ORA
    Admin/technical view (`ora_panel=True`) intentionally keeps real
    filenames and dev terms; the regular (business-owner) chat does
    not. Kept as one function so all sites gate identically (same
    single-surface-drift discipline as apply_no_false_success_guard)."""
    if ora_panel:
        return content
    return filter_for_business_owner(content)


async def apply_business_owner_guards(
    ora_panel: bool, content: str, user_prompt: str = "",
    *, session_id: str = "", user_id: str = "",
) -> str:
    """R1+R2 (2026-08-31) — the SINGLE guard chain every user-facing
    reply passes through, replacing the 4 duplicated call sites this
    used to be spread across in routers/chat.py (code-review finding:
    duplication is a real regression risk for this exact feature — a
    site that drifts out of sync silently re-opens the jargon/dead-end
    bug this whole rework exists to close).

    Order: voice filter -> banned-fallback-phrase strip -> dead-end
    guard -> completeness guard. A real ```aurem-handoff fence (the
    Approve-the-fix button's source) is NEVER touched — R5a regression
    guard, checked FIRST so no caller can forget it.

    Each guard that actually rewrites something also logs a
    self_bug_event (P7) — the model was about to hand the owner a
    jargon leak / dead end / cut-off reply, which is ORA's OWN bug,
    not the owner's website's.
    """
    if not content or "aurem-handoff" in content:
        return content
    content = apply_business_voice(ora_panel, content)
    if ora_panel:
        return content
    from services.bail_reason import strip_banned_fallback_phrases
    from services.no_dead_end_guard import ensure_alternative, has_dead_end
    from services.incomplete_reply_guard import ensure_complete, is_incomplete
    from services import self_bug

    ctx = {"session_id": session_id, "user_id": user_id}
    before = content
    content = strip_banned_fallback_phrases(content, user_prompt)
    if content != before:
        await self_bug.emit("dead_end_leak", before[:300], ctx, source="bail_reason")
    if is_incomplete(content):
        await self_bug.emit("truncated_reply", content[-300:], ctx, source="incomplete_reply_guard")
    content = ensure_complete(content)
    if has_dead_end(content):
        await self_bug.emit("dead_end_leak", content[:300], ctx, source="no_dead_end_guard")
    content = ensure_alternative(content)

    # P8-B/E (2026-08-31) — only checked on an actual design/brand/
    # visual ask (see design_ask_detector.py); a normal fix reply is
    # never touched by this branch, so it can't misfire on unrelated
    # "can't" phrasing.
    from services.design_ask_detector import is_design_ask
    if is_design_ask(user_prompt):
        from services.design_refusal_guard import is_compliant_design_reply, strip_design_refusal
        if not is_compliant_design_reply(content):
            await self_bug.emit("dead_end_leak", content[:300], ctx, source="design_refusal_guard")
            content = strip_design_refusal(content)
    return content
