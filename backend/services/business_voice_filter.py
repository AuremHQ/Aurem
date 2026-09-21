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

# 2026-02 — confirmed live bug (same-engagement founder repro): a
# filename referenced with a leading relative-path dot/slash (e.g.
# ".gif_dimension_checker.py", "./validators.py") lost that leading
# punctuation because `\b` only anchors on a transition INTO a word
# character — a leading "." is itself a non-word char, so the match
# started one character late, leaving the dot/slash orphaned OUTSIDE
# the match and glued directly onto the humanized replacement label
# (".that page", "./that file"). Root cause, not a one-off: this is
# the SAME underlying "regex boundary doesn't account for a real
# filename's own leading punctuation" class as the URL-mangling fix
# above, just on the other end of the filename instead of embedded in
# a larger string. Fix: the negative lookbehind `(?<![\w.])` still
# blocks a match starting mid-identifier (e.g. never matches "app.py"
# out of "myapp.py"), but now explicitly allows — and CONSUMES — an
# optional leading "." / ".." / "./" / "../" as part of the same
# match, so it gets replaced together with the rest of the filename
# instead of being left behind.
_FILENAME_RE = re.compile(
    r"(?<![\w.])\.{0,2}/?[\w\-./]+\.(?:py|jsx?|tsx?|json|md|ya?ml|css|s?css|html?|txt|sql|env|sh)\b",
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


# 2026-09-12 — confirmed live bug (founder repro, SAME turn as the
# "your page" fix above): a commit link's real domain got mangled —
# "https://github.com/OWNER/repo/update/<sha>" came back as
# "https://website's connection.com/...". Root cause: EVERY regex
# pass below (_FILENAME_RE, _combined_pattern's word map, etc.) runs
# over the ENTIRE reply text with zero awareness that some spans are
# URLs, not prose. "github" inside the domain "github.com" is still a
# whole word by `\b` standards, so the map's "github" -> "website's
# connection" entry fires on it exactly like it would in a sentence.
# Any map term, or any dotted-extension the URL path happens to end
# in (e.g. a link ending in `...data.json`), is equally exposed — one
# shared root cause, not two unrelated bugs (confirmed via re-repro).
# Fix: pull every http(s) URL out FIRST, substitute it for a sentinel
# placeholder that cannot collide with any map term or the filename/
# grammar regexes, run every existing pass unchanged on what's left,
# then restore the exact original URL bytes verbatim. A link's
# functional correctness always outranks hiding jargon inside it.
_URL_RE = re.compile(r"https?://\S+")
_URL_PLACEHOLDER = "\x00URL{}\x00"

# 2026-02 round-4 — confirmed live, and MUCH more severe than the two
# bugs above: this whole filter has no concept of a fenced code block.
# When a real source-file excerpt is quoted back to the user (e.g.
# after read_repo_file), EVERY dev-term/filename substitution below
# fires on the CODE ITSELF, not just the prose around it — corrupting
# real Python inside a ``` fence: "Path to GIF file" -> "Path to GIF
# page", `File not found: {x}` -> `Page not found: {x}` (an f-string,
# now lying about what error it raises), `gif_path.stat().st_size` ->
# `gif_path.stat()a project page` (a "file"/"page"-class substitution
# landing mid-expression with no word boundary at all, breaking the
# Python syntax outright), and `except Exception` mangled beyond
# recognition. This is not cosmetic — it silently rewrites the actual
# code the user is looking at. Fix: same technique as URLs/labels
# above, but MUST run FIRST — stash every ``` fenced block, verbatim,
# before anything else in the pipeline gets a chance to touch it.
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
_CODE_PLACEHOLDER = "\x00CODE{}\x00"

# 2026-02 — confirmed live bug (same-engagement founder repro,
# "sql_injection in validators.py" -> ".that page ... .that page"):
# `_humanize_filename` correctly picks `_DEFAULT_FILE_TERM`
# ("that file") for non-page-like extensions like .py/.json/.sql/etc
# — but that output text then runs through the GENERIC dev-term map
# below (_combined_pattern/_replace_term), which has its own
# unrelated entry `"file" -> "page"` (a real business-owner
# simplification for sentences that say "your file" in prose). That
# later pass has zero awareness the word "file" it's looking at was
# JUST produced by the filename step two lines above — it fires on
# it exactly like it would in any other sentence, silently turning
# every single "that file" back into "that page" regardless of what
# `_PAGE_LIKE_EXTS` decided. The `_PAGE_LIKE_EXTS`/`_DEFAULT_FILE_TERM`
# distinction has therefore never actually been effective for ANY
# non-page-like file since it was introduced — same root class as the
# URL fix above (a later, context-blind pass re-processing text an
# earlier pass already finalized). Fix: identical technique — stash
# `_humanize_filename`'s OUTPUT behind its own sentinel placeholder so
# the combined-term pass can't touch it, then restore the real label
# verbatim at the very end, after every other pass has already run.
_LABEL_PLACEHOLDER = "\x00LBL{}\x00"


def filter_for_business_owner(text: str) -> str:
    """Deterministic — same input always produces the same output, no
    LLM call. Strips filenames-with-extension (replaced with a plain
    page reference) and rewrites dev terms to business-owner terms.
    Any embedded URL is passed through byte-for-byte, untouched."""
    if not text:
        return text
    codes: list[str] = []
    urls: list[str] = []
    labels: list[str] = []

    def _stash_code(m: re.Match) -> str:
        codes.append(m.group(0))
        return _CODE_PLACEHOLDER.format(len(codes) - 1)

    def _stash_url(m: re.Match) -> str:
        urls.append(m.group(0))
        return _URL_PLACEHOLDER.format(len(urls) - 1)

    def _stash_label(m: re.Match) -> str:
        labels.append(_humanize_filename(m))
        return _LABEL_PLACEHOLDER.format(len(labels) - 1)

    shielded = _CODE_FENCE_RE.sub(_stash_code, text)
    protected = _URL_RE.sub(_stash_url, shielded)
    out = _FILENAME_RE.sub(_stash_label, protected)
    out = _ACCESS_TO_RE.sub(lambda m: _preserve_case(m.group(0), "I can't see"), out)
    out = _combined_pattern().sub(_replace_term, out)
    out = _fix_a_an(out)
    for i, label in enumerate(labels):
        out = out.replace(_LABEL_PLACEHOLDER.format(i), label)
    for i, url in enumerate(urls):
        out = out.replace(_URL_PLACEHOLDER.format(i), url)
    for i, code in enumerate(codes):
        out = out.replace(_CODE_PLACEHOLDER.format(i), code)
    return out


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
            # 2026-09-21 — Self-Repair audit Part 2.2 follow-up: this
            # 3rd emit call site was missed in the original fix. A
            # reply that's non-compliant with the strict "always
            # commit to 3 concrete directions right now" design
            # template still gets template-corrected below (P8-B's
            # "never refuse" contract is unchanged) — but a reply
            # that's ALSO a legitimate, deliberate refusal (a
            # permission/scope gate, or an honest "you'd want a
            # specialist" redirect) must not be double-counted as a
            # self-repair bug on top of that correction.
            from services.legitimate_refusal import is_legitimate_refusal
            if not is_legitimate_refusal(content):
                await self_bug.emit("dead_end_leak", content[:300], ctx, source="design_refusal_guard")
            content = strip_design_refusal(content)
    return content
