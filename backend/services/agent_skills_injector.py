"""
services/agent_skills_injector.py
==================================

2026-11-25 — ORA audit Part VIII fix.

The 11 editor skills under `.agents/skills/` were static on disk:
zero hits in runtime code, while the 7 Vanguard skills ARE ingested
via `services/skill_context_injector.py`. This module mirrors that
injector exactly (keyword rules + per-skill char caps + max-N picks)
for the `.agents/skills` corpus, so a matching skill's guidance
reaches the prompt context instead of sitting unread.

Deliberate limits (same as the Vanguard injector):
  • NEVER inject a whole SKILL.md — every skill is sliced to its cap
    (1.5–2K chars) with an explicit "…(truncated)" marker.
  • Keyword-gated, max 3 keyword skills per task.
  • `security-trust` is ALWAYS injected first (cap 1200) — the
    security baseline applies to every Mode C task, same role as
    `security-review.md` in the Vanguard injector.
  • Content/SEO skills (seo, structured-data, ai-content-discovery,
    content-semantics) have their own trigger rules and fire on
    content tasks, not code tasks — they are NOT dev-time-only, but
    they only fire when the task actually asks for that work.
  • Missing `.agents/skills` dir or unreadable file → "" (never raise,
    never break a turn — same fail-soft contract as the Vanguard
    loader).

Wiring: `skill_context_injector.build_skill_context()` appends this
module's block, so all 4 existing call sites (CoderAgent.write,
orchestrator.build_persona, worker_api._run_task/_run_task_via_api)
inherit it with no call-site edits.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import List

_AGENTS_SKILLS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", ".agents", "skills"
)

# Trigger keywords → skill dir, slice limit (chars).
# Keywords come from each SKILL.md's own front-matter description
# ("Use when asked to …") at main 097697ba.
_INJECTION_RULES: list[tuple[list[str], str, int]] = [
    (
        ["webmcp", "a2a", "agent card", "mcp server card", "openapi",
         "api-catalog", "api catalog", "nlweb", "agent protocols",
         "agent payments", "x402", "mcp apps", "agent commerce"],
        "agent-protocols", 2000,
    ),
    (
        ["agent-ready", "agent readability", "agent-readability",
         "readability scan", "readability score"],
        "agent-ready-api", 1800,
    ),
    (
        ["agent-ready cli", "agent-ready get", "agent-ready list",
         "agent-ready ask", "terminal scan"],
        "agent-ready-cli", 1800,
    ),
    (
        ["agent-ready mcp", "agent-ready as an mcp", "scan_site",
         "install agent-ready mcp"],
        "agent-ready-mcp", 1800,
    ),
    (
        ["robots.txt", "llms.txt", "sitemap", "ai crawler",
         "content discovery", "soft 404", "agents.md",
         "markdown to agents", "crawler directives"],
        "ai-content-discovery", 2000,
    ),
    (
        ["semantic html", "heading hierarchy", "alt text", "aria",
         "accessibility", "server-side rendering", "ssr",
         "form label", "autocomplete", "keyboard navigation",
         "content semantics", "semantic landmark"],
        "content-semantics", 2000,
    ),
    (
        ["desktop qa", "desktop layout", "desktop viewport",
         "desktop polish", "window-controls-overlay",
         "no horizontal overflow"],
        "desktop-qa", 1500,
    ),
    (
        ["mobile qa", "mobile responsive", "tap target",
         "viewport overflow", "lighthouse pwa", "mobile layout",
         "responsive qa"],
        "mobile-responsive-qa", 1800,
    ),
    (
        ["seo", "search console", "organic traffic", "keyword",
         "backlink", "click-through", "ctr", "indexing",
         "google traffic", "search traffic"],
        "seo", 2000,
    ),
    (
        ["json-ld", "json ld", "schema.org", "schema markup",
         "structured data", "faq schema", "organization schema",
         "product markup"],
        "structured-data", 2000,
    ),
]

# Maximum 3 keyword skills per task — mirrors _MAX_SKILLS_PER_TASK.
_MAX_AGENT_SKILLS_PER_TASK = 3

# Always injected FIRST for every task (security baseline, same role
# as security-review.md in the Vanguard injector). Small cap: the
# most generic skill gets the smallest slice.
_ALWAYS_INJECT = ("security-trust", 1200)


@lru_cache(maxsize=32)
def _load_agent_skill(skill_dir: str, char_cap: int) -> str:
    """Disk-load `<skill_dir>/SKILL.md` once, then memoise. Returns
    sliced content (never the whole file). "" when missing/unreadable."""
    path = os.path.abspath(os.path.join(_AGENTS_SKILLS_DIR, skill_dir, "SKILL.md"))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if len(text) > char_cap:
            text = text[:char_cap] + "\n…(truncated)"
        return text
    except (FileNotFoundError, OSError):
        return ""
    except Exception:
        return ""


def select_agent_skills(task_description: str) -> List[tuple[str, str]]:
    """Returns list of (skill_name, sliced_content) to inject.
    `security-trust` is always first; then up to 3 keyword matches."""
    task = (task_description or "").lower()
    picked: list[tuple[str, str]] = []
    seen: set[str] = set()
    # Security baseline first.
    always = _load_agent_skill(*_ALWAYS_INJECT)
    if always:
        picked.append((_ALWAYS_INJECT[0], always))
        seen.add(_ALWAYS_INJECT[0])
    keyword_picks: list[tuple[str, str]] = []
    for triggers, skill_dir, cap in _INJECTION_RULES:
        if skill_dir in seen:
            continue
        if any(kw in task for kw in triggers):
            content = _load_agent_skill(skill_dir, cap)
            if content:
                keyword_picks.append((skill_dir, content))
                seen.add(skill_dir)
        if len(keyword_picks) >= _MAX_AGENT_SKILLS_PER_TASK:
            break
    picked.extend(keyword_picks)
    return picked


def build_agent_skill_context(task_description: str) -> str:
    """Returns a single markdown block ready to slot into a system
    prompt. Empty string when no skill files load (no token overhead,
    never raises)."""
    try:
        picks = select_agent_skills(task_description)
    except Exception:
        return ""
    if not picks:
        return ""
    parts = ["[AGENT SKILLS (.agents/skills) — apply these patterns by default]"]
    for name, content in picks:
        title = name.replace("-", " ").upper()
        parts.append(f"\n### {title}\n{content}")
    return "\n".join(parts)
