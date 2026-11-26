"""
tests/test_part89_agent_skills.py — Part VIII audit fix: the 11
`.agents/skills` were static on disk; the new
services/agent_skills_injector.py mirrors skill_context_injector.py
(keyword rules + slicing/caps) and is wired into
build_skill_context() so all 4 call sites inherit it.

Offline unit tests — real `.agents/skills/*/SKILL.md` files seeded
in tree/.agents/skills (from main 097697ba). Run from backend/ with
the agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part89_agent_skills.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from services import agent_skills_injector as asi  # noqa: E402
from services.skill_context_injector import build_skill_context  # noqa: E402


def test_security_trust_reaches_prompt_for_matching_request():
    """A security-headers task gets the security-trust slice in the
    prompt context — the skill's own words (HSTS/CSP), not a stub."""
    ctx = asi.build_agent_skill_context(
        "fix the security headers: add HSTS and configure CSP")
    assert "Content-Security-Policy" in ctx
    assert "HSTS" in ctx
    assert "AGENT SKILLS" in ctx


def test_seo_skill_reaches_prompt_for_seo_task():
    ctx = asi.build_agent_skill_context(
        "why did our Google traffic drop — check Search Console")
    assert "### SEO" in ctx
    assert "Search Console" in ctx


def test_structured_data_skill_reaches_prompt_for_json_ld_task():
    ctx = asi.build_agent_skill_context("add JSON-LD Organization schema markup")
    assert "### STRUCTURED DATA" in ctx


def test_oversized_skill_files_are_sliced_not_injected_whole():
    """agent-protocols/SKILL.md is 22,146 chars on disk. A matching
    task must get a ~2K slice, never the whole file."""
    content = asi._load_agent_skill("agent-protocols", 2000)
    assert 0 < len(content) <= 2000 + len("\n…(truncated)")
    assert content.endswith("…(truncated)")
    ctx = asi.build_agent_skill_context(
        "add WebMCP tool annotations and an A2A agent card")
    assert "### AGENT PROTOCOLS" in ctx
    assert len(ctx) < 9000  # always (1200) + 3 keyword slices max
    assert "MCP" in ctx


def test_security_trust_is_first_and_always_present():
    """security-trust is the always-inject baseline (mirrors
    security-review.md): first in the list, even for a task that
    matches no keyword."""
    picks = asi.select_agent_skills("write a poem about cats")
    assert picks, "always-inject must fire even with no keyword match"
    assert picks[0][0] == "security-trust"


def test_max_three_keyword_skills_per_task():
    """A task matching many skills still gets at most 3 keyword
    picks (+ the always-inject baseline)."""
    picks = asi.select_agent_skills(
        "add JSON-LD schema, fix robots.txt and sitemap, improve SEO "
        "Search Console traffic, fix semantic HTML alt text, add HSTS")
    keyword_picks = [name for name, _ in picks if name != "security-trust"]
    assert len(keyword_picks) <= 3


def test_missing_skills_dir_fails_soft():
    """A deployment without .agents/skills must not break a turn."""
    orig = asi._AGENTS_SKILLS_DIR
    asi._load_agent_skill.cache_clear()
    try:
        asi._AGENTS_SKILLS_DIR = str(_BACKEND_DIR / "does-not-exist-skills")
        assert asi.build_agent_skill_context("add HSTS headers") == ""
    finally:
        asi._AGENTS_SKILLS_DIR = orig
        asi._load_agent_skill.cache_clear()


def test_build_skill_context_includes_agent_block():
    """The wiring: build_skill_context() (the function all 4 call
    sites use) now carries the .agents/skills block alongside the
    Vanguard block — a relevant skill's content reaches the prompt
    context for a matching request."""
    ctx = build_skill_context("fix the security headers and add HSTS")
    assert "VANGUARD SECURITY SKILLS" in ctx
    assert "AGENT SKILLS (.agents/skills)" in ctx
    assert "Content-Security-Policy" in ctx
