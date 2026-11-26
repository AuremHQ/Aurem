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

2026-11-26 — Superpowers (obra/superpowers v7.0.2, pinned
22f0b463): 14 of the 15 skills vendored under the same
`.agents/skills/` home. Their keyword rules live in
`_SUPERPOWERS_RULES` (listed first — the workflow skills win ties
under the shared 3-pick cap). `using-superpowers` is the bootstrap:
always injected at task start (like `security-trust`), because it
is what tells the agent HOW to use the skills. The repo-add
workflow (`build_repo_add_workflow_context`) composes the
brainstorm → plan → TDD → verify block for a newly added repo's
first task; see `REPO_ADD_WORKFLOW_SKILLS`.

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
#
# 2026-11-26 — Superpowers rules first (workflow order: brainstorm →
# plan → TDD → execute → debug → verify → review → finish). They win
# ties under the shared 3-pick cap; the editor rules below are
# unchanged.
_SUPERPOWERS_RULES: list[tuple[list[str], str, int]] = [
    (
        ["brainstorm", "new feature", "add functionality",
         "create a feature", "build a component", "explore options",
         "planning a new"],
        "brainstorming", 2000,
    ),
    (
        ["write a plan", "implementation plan", "multi-step",
         "plan the implementation", "break it down", "create a plan"],
        "writing-plans", 1800,
    ),
    (
        ["tdd", "test-driven", "test first", "write tests first",
         "implement a feature", "implement the feature", "bugfix",
         "bug fix"],
        "test-driven-development", 1800,
    ),
    (
        ["execute the plan", "implement the plan", "follow the plan",
         "execute plan"],
        "executing-plans", 2000,
    ),
    (
        ["subagent", "delegate", "parallel agents",
         "independent tasks"],
        "subagent-driven-development", 2000,
    ),
    (
        ["2+ independent", "independent tasks in parallel",
         "work on these in parallel"],
        "dispatching-parallel-agents", 1500,
    ),
    (
        ["debug", "failing test", "test failure",
         "unexpected behavior", "not working", "broken", "bug"],
        "systematic-debugging", 1800,
    ),
    (
        ["verify before", "before committing", "before creating a pr",
         "claim complete", "confirm it passes", "verify the fix",
         "verify it works"],
        "verification-before-completion", 1500,
    ),
    (
        ["request a review", "review my work", "code review",
         "before merging", "verify the work"],
        "requesting-code-review", 1500,
    ),
    (
        ["review feedback", "reviewer said", "review comments",
         "address the review", "code review feedback"],
        "receiving-code-review", 1500,
    ),
    (
        ["finish the branch", "ready to merge", "integrate the work",
         "open a pull request", "create a pr", "draft pr",
         "implementation is complete"],
        "finishing-a-development-branch", 1500,
    ),
    (
        ["worktree", "isolated workspace", "git worktree"],
        "using-git-worktrees", 1500,
    ),
    (
        ["went wrong", "repeated work", "ignored plan",
         "why is it so expensive", "what is it doing", "took too long",
         "skill didn't fire", "diagnose the session"],
        "diagnosing-superpowers", 1500,
    ),
]

_EDITOR_RULES: list[tuple[list[str], str, int]] = [
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

# One merged rule list — Superpowers rules first (workflow order),
# editor rules after, unchanged. Shared 3-pick cap (below).
_INJECTION_RULES: list[tuple[list[str], str, int]] = (
    _SUPERPOWERS_RULES + _EDITOR_RULES
)

# Maximum 3 keyword skills per task — mirrors _MAX_SKILLS_PER_TASK.
_MAX_AGENT_SKILLS_PER_TASK = 3

# Always injected FIRST for every task (security baseline, same role
# as security-review.md in the Vanguard injector). Small cap: the
# most generic skill gets the smallest slice.
_ALWAYS_INJECT = ("security-trust", 1200)

# 2026-11-26 — Superpowers bootstrap: `using-superpowers` tells the
# agent HOW to find and use skills, so it is always injected (after
# security-trust) at every task start — Ora has no SessionStart hook,
# and a task start is the closest equivalent. Small cap: only the
# bootstrap section is needed, the skill itself is 2.8K chars.
_ALWAYS_INJECT_SUPERPOWERS = ("using-superpowers", 1500)

# Repo-add workflow skills (brainstorm → plan → TDD → verify), in
# workflow order. `build_repo_add_workflow_context` composes these
# slices for a newly added repo's first task; worker_api passes them
# as `exclude_agent_skills` so the keyword loop doesn't inject the
# same slices twice on that task.
REPO_ADD_WORKFLOW_SKILLS = (
    "brainstorming",
    "writing-plans",
    "test-driven-development",
    "verification-before-completion",
)

# Per-skill slice caps for the repo-add workflow block. Smaller than
# the keyword caps — the block is a once-per-repo first-task
# injection, not an every-task cost.
_REPO_ADD_WORKFLOW_CAPS = {
    "brainstorming": 1500,
    "writing-plans": 1500,
    "test-driven-development": 1500,
    "verification-before-completion": 1200,
}


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


def select_agent_skills(
    task_description: str,
    exclude_agent_skills=None,
) -> List[tuple[str, str]]:
    """Returns list of (skill_name, sliced_content) to inject.
    `security-trust` is always first; then up to 3 keyword matches.
    `exclude_agent_skills` (names) skips those dirs in the keyword
    loop — used by the repo-add first task so the workflow block
    doesn't duplicate the same slices."""
    task = (task_description or "").lower()
    excluded = set(exclude_agent_skills or ())
    picked: list[tuple[str, str]] = []
    seen: set[str] = set()
    # Security baseline first.
    always = _load_agent_skill(*_ALWAYS_INJECT)
    if always:
        picked.append((_ALWAYS_INJECT[0], always))
        seen.add(_ALWAYS_INJECT[0])
    keyword_picks: list[tuple[str, str]] = []
    for triggers, skill_dir, cap in _INJECTION_RULES:
        if skill_dir in seen or skill_dir in excluded:
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


def build_agent_skill_context(
    task_description: str,
    exclude_agent_skills=None,
) -> str:
    """Returns a single markdown block ready to slot into a system
    prompt. Empty string when no skill files load (no token overhead,
    never raises). The Superpowers bootstrap (`using-superpowers`)
    leads the block so the agent knows HOW to use the skills that
    follow; `exclude_agent_skills` is passed through to the keyword
    loop (repo-add first task)."""
    try:
        picks = select_agent_skills(task_description, exclude_agent_skills)
        bootstrap = _load_agent_skill(*_ALWAYS_INJECT_SUPERPOWERS)
    except Exception:
        return ""
    if not picks and not bootstrap:
        return ""
    parts = []
    if bootstrap:
        parts.append(
            "[SUPERPOWERS BOOTSTRAP — how to use skills: invoke the "
            "matching skill before ANY response, including clarifying "
            "questions]\n\n### USING SUPERPOWERS\n" + bootstrap
        )
    if picks:
        parts.append(
            "[AGENT SKILLS (.agents/skills) — apply these patterns by default]"
        )
        for name, content in picks:
            title = name.replace("-", " ").upper()
            parts.append(f"\n### {title}\n{content}")
    return "\n".join(parts)


def build_repo_add_workflow_context() -> str:
    """Repo-add workflow block for a newly added repo's FIRST task
    only (flag `superpowers_first_task` on the project doc, set at
    POST /projects/add, cleared by the worker when consumed).
    Composes the brainstorm → plan → TDD → verify slices from the
    vendored Superpowers skills. Empty string when the skill files
    don't load (fail-soft — the task still runs, just without the
    workflow block)."""
    try:
        sections = []
        for skill_dir in REPO_ADD_WORKFLOW_SKILLS:
            content = _load_agent_skill(
                skill_dir, _REPO_ADD_WORKFLOW_CAPS[skill_dir])
            if content:
                title = skill_dir.replace("-", " ").upper()
                sections.append(f"\n### {title}\n{content}")
        if not sections:
            return ""
    except Exception:
        return ""
    header = (
        "[NEW REPO ADDED — SUPERPOWERS WORKFLOW — this is the first "
        "task on a repo that was just added to the project. Run the "
        "workflow below in order: brainstorm the goal first, then "
        "write the plan, then implement test-first (TDD), then verify "
        "before claiming anything is done. Ship via a draft PR on a "
        "throwaway branch — never push directly to the user's live "
        "branch. Ora's approval gates still apply; prefer "
        "executing-plans over subagent-driven-development (token "
        "budget).]"
    )
    return header + "".join(sections)
