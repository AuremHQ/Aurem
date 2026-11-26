"""
services/skill_context_injector.py
==================================

Iter 44 — Vanguard skill injection.

When a Mode C task is auth-related, payment-related, frontend, or any
new API endpoint, we inject a short slice of the matching Vanguard
skill file into the AI's system prompt. Zero LLM cost — pure prompt
augmentation — and ORA writes more secure code by default.

Skills sourced from Antigravity Awesome Skills (May 2026).
Stored at: /app/backend/vanguard_skills/*.md

Each skill is capped (per-skill char budget) and we inject at most
N skills per task to keep total budget < ~3K tokens.
"""
from __future__ import annotations
import os
import re
from functools import lru_cache
from typing import List

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "vanguard_skills")

# Trigger keywords → skill file, slice limit (chars)
_INJECTION_RULES: list[tuple[list[str], str, int]] = [
    (
        ["login", "auth", "jwt", "oauth", "session", "password", "signin",
         "signup", "register", "rbac", "permissions", "role-based"],
        "auth-implementation.md", 2500,
    ),
    (
        ["api", "endpoint", "route", "fastapi", "rest", "graphql",
         "rate limit", "cors", "validation"],
        "api-security.md", 2000,
    ),
    (
        ["stripe", "payment", "billing", "checkout", "invoice",
         "subscription", "webhook", "razorpay", "paypal", "card",
         "credit card", "debit card", "pan", "cvv", "pci"],
        "pci-compliance.md", 2200,
    ),
    (
        ["gdpr", "ccpa", "dpdp", "privacy", "personal data", "pii",
         "user data", "data export", "right to be forgotten",
         "data deletion", "consent", "cookie banner", "data retention"],
        "privacy-by-design.md", 2200,
    ),
    (
        ["react", "component", "jsx", "tsx", "frontend", "ui", "form",
         "dangerouslysetinnerhtml", "xss"],
        "frontend-security.md", 1800,
    ),
    (
        ["backend", "fastapi", "django", "flask", "middleware", "header",
         "server", "uvicorn"],
        "backend-security.md", 1800,
    ),
]

# Maximum 3 skills per task — Iter 51 raised from 2 to make room for the
# new PCI + Privacy skills landing alongside auth/api in payment+user
# flows. Total budget stays under ~7K chars (well within context window).
_MAX_SKILLS_PER_TASK = 3

# Iter 44 — Always-inject security checklist for Mode C (any code task).
# Cap small (1000 chars) since it's the most generic.
_ALWAYS_INJECT = ("security-review.md", 1000)


@lru_cache(maxsize=16)
def _load_skill_cached(filename: str, char_cap: int, mtime: float) -> str:
    """Disk-load a skill file once per mtime, then memoise. Returns
    sliced content. Part XII (b3): the cache key includes the file's
    mtime — see `_load_skill` for why the old error-caching shape
    was wrong."""
    path = os.path.abspath(os.path.join(_SKILLS_DIR, filename))
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if len(text) > char_cap:
        text = text[:char_cap] + "\n…(truncated)"
    return text


def _load_skill(filename: str, char_cap: int) -> str:
    """Disk-load a skill file, memoised by (filename, cap, mtime).

    Part XII (b3): the old `@lru_cache` sat directly on this loader,
    so a load ERROR (`""`) was cached for the life of the process —
    a skill file created later on disk was never picked up, and an
    edited skill was invisible until restart. Now a missing file
    returns `""` WITHOUT touching the cache, and an edit changes the
    mtime key, so the new content loads on the next call."""
    path = os.path.abspath(os.path.join(_SKILLS_DIR, filename))
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return ""
    try:
        return _load_skill_cached(filename, char_cap, mtime)
    except OSError:
        return ""


# Back-compat for callers/tests that cleared the old loader cache.
_load_skill.cache_clear = _load_skill_cached.cache_clear  # type: ignore[attr-defined]
_load_skill.cache_info = _load_skill_cached.cache_info  # type: ignore[attr-defined]


def _matches_keyword(task: str, kw: str) -> bool:
    """Word-boundary keyword match (Part XII b2). A bare `in` check
    let `"ui"` match `"build"`/`"require"`/`"suite"` and `"rest"`
    match `"restart"`/`"interest"` — pure false positives. `\\b`
    handles single words and multi-word triggers (`"rate limit"`,
    `"credit card"`) alike."""
    if not kw:
        return False
    return re.search(r"\b" + re.escape(kw) + r"\b", task) is not None


def select_skills(task_description: str) -> List[tuple[str, str]]:
    """Returns list of (skill_name, sliced_content) to inject."""
    task = (task_description or "").lower()
    picked: list[tuple[str, str]] = []
    seen_files: set[str] = set()
    for triggers, filename, cap in _INJECTION_RULES:
        if filename in seen_files:
            continue
        if any(_matches_keyword(task, kw) for kw in triggers):
            content = _load_skill(filename, cap)
            if content:
                picked.append((filename, content))
                seen_files.add(filename)
        if len(picked) >= _MAX_SKILLS_PER_TASK:
            break
    # Always tack on the small security-review checklist
    always = _load_skill(*_ALWAYS_INJECT)
    if always and _ALWAYS_INJECT[0] not in seen_files:
        picked.append((_ALWAYS_INJECT[0], always))
    return picked


def build_skill_context(task_description: str) -> str:
    """Returns a single markdown block ready to slot into a system prompt.
    Empty string when no skills match (no token overhead)."""
    picks = select_skills(task_description)
    if not picks:
        return ""
    parts = ["[VANGUARD SECURITY SKILLS — apply these patterns by default]"]
    for name, content in picks:
        title = name.replace("-", " ").replace(".md", "").upper()
        parts.append(f"\n### {title}\n{content}")
    # 2026-11-25 — ORA audit Part VIII: the 11 `.agents/skills` were
    # static on disk. Append their (keyword-gated, sliced) block so
    # all 4 call sites of this function inherit it. Fail-soft: a
    # missing dir or import problem must never break a turn.
    try:
        from services.agent_skills_injector import build_agent_skill_context
        agent_block = build_agent_skill_context(task_description)
    except Exception:
        agent_block = ""
    if agent_block:
        parts.append(f"\n{agent_block}")
    return "\n".join(parts)
