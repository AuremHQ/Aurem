"""
services/knowledge_injector.py — ORA Brain Part 1, Phase A.

L1 global knowledge injection. Same shape as services/
skill_context_injector.py (keyword-triggered, char-capped, fixed
order) — this module is the ENGINEERING-ANSWER analogue for
backend/knowledge/*.md instead of backend/vanguard_skills/*.md.

Hard safety rules (tested):
  - Only files with front-matter `status: approved` are ever loaded.
    Every file ships as `draft` — nothing is injected until a human
    (the founder) flips the flag. This module never writes that flag.
  - The candidate file list is a FIXED, hardcoded tuple — no path is
    ever built from user/task input, so there is no path-traversal
    surface regardless of what the task string contains.
  - `marketing-style.md` is excluded from the candidate list by name,
    not merely by an empty `inject_on` — it can never reach an
    engineering prompt through this module.
  - Total injected content is hard-capped (see _TOTAL_CHAR_CAP) —
    matches skill_context_injector's "~3K tokens" budget framing.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

_KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge")

# Fixed, hardcoded candidate list — never derived from task/user input.
# marketing-style.md is deliberately NOT here (see module docstring).
_CANDIDATE_FILES: tuple[str, ...] = (
    "product.md",
    "pricing.md",
    "common-fixes.md",
    "security-checks.md",
)

# ~3K tokens, same budget framing as skill_context_injector.py.
_TOTAL_CHAR_CAP = 12000

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Returns (meta_dict, body). Never raises — garbage/missing/huge/
    unicode front-matter all degrade to an empty meta dict (treated as
    not-approved, so it's simply never injected)."""
    if not isinstance(text, str) or not text:
        return {}, ""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        import yaml
        meta = yaml.safe_load(m.group(1))
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    return meta, text[m.end():]


@lru_cache(maxsize=32)
def _load_file_cached(filename: str, mtime: float) -> tuple[dict, str]:
    """Disk-load + parse, memoised keyed by (filename, mtime).

    Part XII (b3): the old `@lru_cache` sat directly on the loader,
    so a load ERROR (`{}, ""`) was cached forever — a file that
    appeared (or was flipped draft → approved) later was invisible
    for the life of the process. Now the cache key includes the
    file's mtime: a missing file never reaches this function (see
    `_load_file`), and an edit changes the key, so the new content
    is picked up with no `cache_clear()` and no restart.
    """
    path = os.path.abspath(os.path.join(_KNOWLEDGE_DIR, filename))
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return _parse_frontmatter(text)


def _load_file(filename: str) -> tuple[dict, str]:
    """Disk-load + parse once per mtime, memoised. filename MUST be one of
    _CANDIDATE_FILES — callers never pass task/user-derived strings
    here, so os.path.join below can never escape _KNOWLEDGE_DIR.

    Failures are NEVER cached: a missing/unreadable file returns
    `({}, "")` without touching the cache, so a file that appears
    later is picked up on the next call."""
    path = os.path.abspath(os.path.join(_KNOWLEDGE_DIR, filename))
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}, ""
    try:
        return _load_file_cached(filename, mtime)
    except OSError:
        return {}, ""


# Back-compat for callers/tests that cleared the old loader cache.
_load_file.cache_clear = _load_file_cached.cache_clear  # type: ignore[attr-defined]
_load_file.cache_info = _load_file_cached.cache_info  # type: ignore[attr-defined]


def _matches_keyword(task: str, kw: str) -> bool:
    """Word-boundary keyword match (Part XII b2). A bare `in` check
    let `"rest"` match `"restart"`/`"interest"` and `"ui"` match
    `"build"` — pure false positives. `\\b` handles single words
    and multi-word triggers (`"rate limit"`) alike."""
    if not kw:
        return False
    return re.search(r"\b" + re.escape(kw) + r"\b", task) is not None


def select_knowledge(task_description: str) -> list[tuple[str, str]]:
    """Returns [(filename, capped_body), ...] for approved files whose
    inject_on keywords match the task, in _CANDIDATE_FILES order.
    Draft (or malformed/missing front-matter) files are always
    skipped, regardless of keyword match."""
    task = (task_description or "").lower()
    picked: list[tuple[str, str]] = []
    total = 0
    for filename in _CANDIDATE_FILES:
        meta, body = _load_file(filename)
        if meta.get("status") != "approved":
            continue
        triggers = meta.get("inject_on") or []
        if not isinstance(triggers, list):
            continue
        if not any(isinstance(kw, str) and _matches_keyword(task, kw.lower()) for kw in triggers):
            continue
        cap = meta.get("max_chars")
        cap = cap if isinstance(cap, int) and cap > 0 else len(body)
        sliced = body.strip()[:cap]
        if not sliced:
            continue
        remaining_budget = _TOTAL_CHAR_CAP - total
        if remaining_budget <= 0:
            break
        sliced = sliced[:remaining_budget]
        picked.append((filename, sliced))
        total += len(sliced)
    return picked


_EXAMPLES_DIR = os.path.join(_KNOWLEDGE_DIR, "examples")
_EXAMPLE_SECTION_RE = {
    "msg": re.compile(r"##\s*Task\s*\n(.*?)(?=\n##|\Z)", re.DOTALL | re.IGNORECASE),
    "reply": re.compile(r"##\s*Good response pattern\s*\n(.*?)(?=\n##|\Z)", re.DOTALL | re.IGNORECASE),
}


def _list_example_files() -> list[str]:
    """Real directory listing at call time (the founder can add/
    approve new examples without a code change) — every name returned
    comes from os.listdir itself, never from task/user input, so
    joining it onto _EXAMPLES_DIR stays traversal-safe the same way
    _CANDIDATE_FILES does."""
    try:
        return sorted(f for f in os.listdir(_EXAMPLES_DIR) if f.endswith(".md"))
    except Exception:
        return []


def _split_example_body(body: str) -> tuple[str, str]:
    msg_m = _EXAMPLE_SECTION_RE["msg"].search(body)
    reply_m = _EXAMPLE_SECTION_RE["reply"].search(body)
    msg = msg_m.group(1).strip() if msg_m else ""
    reply = reply_m.group(1).strip() if reply_m else ""
    return msg, reply


def load_approved_examples(task_description: str, limit: int = 3) -> list[dict]:
    """ORA Brain Part 1, Phase C — L3 approved examples.

    Returns `[{"msg": ..., "reply": ...}, ...]` from
    backend/knowledge/examples/*.md whose front-matter says
    `status: approved`, matched by `task_type` substring against the
    task description. NEVER includes a draft file, and NEVER pulls
    from `ora_council_logs` — a logged row can only ever reach L3 by
    a human manually curating a new file here; this function has no
    write path and never auto-promotes anything."""
    task = (task_description or "").lower()
    # Part XII (b1): limit<=0 must inject ZERO examples. The old loop
    # appended first and checked `len(picked) >= max(0, limit)` second,
    # so limit=0 returned exactly 1 example whenever one matched.
    if limit <= 0:
        return []
    picked: list[dict] = []
    for filename in _list_example_files():
        path = os.path.abspath(os.path.join(_EXAMPLES_DIR, filename))
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        meta, body = _parse_frontmatter(text)
        if meta.get("status") != "approved":
            continue
        task_type = meta.get("task_type")
        # Part XII (b2): word-boundary match, not substring — a
        # task_type of "rest" must not match a task about "restart".
        if isinstance(task_type, str) and task_type and not _matches_keyword(task, task_type.lower()):
            continue
        msg, reply = _split_example_body(body)
        if not msg or not reply:
            continue
        picked.append({"msg": msg, "reply": reply})
        if len(picked) >= max(0, limit):
            break
    return picked


def build_knowledge_context(task_description: str) -> str:
    """Returns a single markdown block ready to slot into a system
    prompt. Empty string when nothing approved matches (no token
    overhead, same contract as build_skill_context)."""
    picks = select_knowledge(task_description)
    if not picks:
        return ""
    parts = ["[ORA KNOWLEDGE — approved facts only. If a fact is not here, say unknown.]"]
    for name, content in picks:
        title = name.replace("-", " ").replace(".md", "").upper()
        parts.append(f"\n### {title}\n{content}")
    return "\n".join(parts)


# ── Phase D — one context builder + ORA_KNOWLEDGE_MODE ─────────────────
_KNOWLEDGE_MODE_VALUES = ("off", "shadow", "on")

_UNKNOWN_FACT_RULE = (
    "Use only the provided knowledge and project notes for facts. If "
    "a fact is missing, say it is unknown. Do not invent prices, "
    "dates or promises."
)


def knowledge_mode() -> str:
    """ORA_KNOWLEDGE_MODE env, default 'off'. An unrecognised value
    fails SAFE to 'off' — same fail-safe convention as
    services.wallet.billing_mode()."""
    raw = (os.environ.get("ORA_KNOWLEDGE_MODE") or "off").strip().lower()
    if raw not in _KNOWLEDGE_MODE_VALUES:
        return "off"
    return raw


async def build_answer_context(
    user_id: str | None, project_id: str | None, task: str,
) -> tuple[str, list[str]]:
    """ORA Brain Part 1, Phase D — THE shared context builder, called
    from every existing injection site instead of each site
    reimplementing this assembly. Returns (block, files_used).
    `files_used` is always filenames only — never file content, safe
    to log.

    Mode contract:
      - off (default; any unrecognised env value also lands here):
        returns ("", []) unconditionally. A caller that only appends
        the block when it's non-empty (the existing convention at
        every current injection site) gets a prompt that is BYTE-
        IDENTICAL to before this function existed.
      - shadow: computes the real knowledge block and files_used (so
        the computation and its logging are exercised and testable)
        but returns ("", files_used) — the empty block preserves the
        same byte-identical-prompt guarantee as off; only logs change.
      - on: returns the real block — approved L1 knowledge (if any
        matches the task) + an empty PROJECT NOTES section (Part 2
        fills this with real per-user project memory; Part 1
        deliberately returns nothing here rather than inventing
        content) + the fixed unknown-fact rule — and the real
        files_used list.

    Never includes few-shot examples. Those remain exclusively
    services.ora_council_retriever.get_council_few_shot's job (L3-
    enhanced in Phase C) — this function and that one are called
    SEPARATELY at each site and their outputs are simply concatenated
    by the caller, so the two systems can never duplicate the same
    few-shot content in one prompt.
    """
    mode = knowledge_mode()
    if mode == "off":
        return "", []

    picks = select_knowledge(task)
    files_used = [name for name, _ in picks]
    knowledge_block = build_knowledge_context(task)

    if mode == "shadow":
        import logging
        logging.getLogger(__name__).info(
            "ora_brain.knowledge_shadow user_id=%s project_id=%s files=%s",
            user_id, project_id, files_used,
        )
        return "", files_used

    # mode == "on"
    parts = []
    if knowledge_block:
        parts.append(knowledge_block)

    # ORA Brain Part 2, Phase B — per-user project memory, rendered as
    # an explicitly-untrusted block. Best-effort: any failure here
    # (no db, no project_id, project not owned by user_id) just omits
    # the section rather than touching the rest of the context.
    try:
        from cto_services.db import get_db as _get_db
        from services.project_memory import (
            format_project_notes_block,
            get_project_memory,
        )
        db = _get_db()
        if db is not None and project_id:
            memory = await get_project_memory(db, user_id, project_id)
            notes_block = format_project_notes_block(memory)
            if notes_block:
                parts.append(notes_block)
    except Exception as e:                                     # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug(
            "project memory lookup skipped: %r", e,
        )

    parts.append(_UNKNOWN_FACT_RULE)
    return "\n\n".join(parts), files_used
