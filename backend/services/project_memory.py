"""
services/project_memory.py — Check Library v2 L0.1: does the project carry
a rules file that tells an AI assistant how this app works, and if not, a
deterministic draft built from what we can see in the repo.

The draft is only ever a PREVIEW here: nothing is written to the user's
repo without their click.  One canonical `AGENTS.md`; `CLAUDE.md` just
points at it so the two never drift apart.  The text is ours (C11); the
only free-form part is the one sentence the user gives about their app.
"""
from __future__ import annotations

import json
import re

from services.checkup_common import _files, detect_stack
from services.checkup_meta import repo_paths

_RULE_FILES = ("agents.md", "claude.md", "gemini.md", ".cursorrules", ".windsurfrules", "conventions.md")
_RULE_PATHS = ("github/copilot-instructions.md",)
_SCRIPT_ORDER = ("dev", "start", "build", "test", "lint", "typecheck")
_PURPOSE_PLACEHOLDER = "_Describe in one or two sentences what this app does and who it is for._"


def has_project_memory(paths: list[str]) -> bool:
    for raw in paths:
        p = raw.lstrip("/").lower()
        if p.count("/") == 0 and p in _RULE_FILES:
            return True
        if p.startswith(".cursor/rules/") or any(p.endswith(r) and p.startswith(".") for r in _RULE_PATHS):
            return True
    return False


def _package_scripts(tc: dict[str, str]) -> dict[str, str]:
    for path, text in _files(tc):
        if path.lstrip("/") == "package.json":
            try:
                scripts = json.loads(text).get("scripts") or {}
            except (ValueError, AttributeError):
                return {}
            return {k: scripts[k] for k in _SCRIPT_ORDER if k in scripts}
    return {}


def _python_commands(tc: dict[str, str]) -> list[str]:
    reqs = " ".join(t for p, t in _files(tc) if p.endswith(("requirements.txt", "pyproject.toml"))).lower()
    return [cmd for word, cmd in (("pytest", "pytest"), ("ruff", "ruff check .")) if re.search(rf"\b{word}\b", reqs)]


def _command_lines(tc: dict[str, str]) -> list[str]:
    lines = [f"- `npm run {name}` — runs `{cmd}`" for name, cmd in _package_scripts(tc).items()]
    lines += [f"- `{cmd}`" for cmd in _python_commands(tc)]
    return lines or ["- _Add the commands to start, test and check this project._"]


def _stack_lines(tc: dict[str, str]) -> list[str]:
    stack = detect_stack(tc)
    lines = [f"- Detected stack: {stack['label']}"]
    if stack["supabase"]:
        lines.append("- Database changes are new files in `supabase/migrations/`; never edit one that already ran.")
    return lines


_RULES = (
    "- Never put passwords, API keys or tokens in code; read them from environment settings and keep `.env` out of git.",
    "- Check and limit every input at the point it enters the server (type, size, who is asking).",
    "- Every change to stored data ships as a migration, with a way to undo it.",
    "- Run the tests and checks above before calling a task done, and say what you could not run.",
    "- Keep changes small and focused; don't rewrite files or remove code unrelated to the task.",
)


def build_project_memory(tc: dict[str, str], purpose: str = "") -> dict[str, str]:
    """{path: content} preview of the files we would propose adding."""
    purpose = " ".join((purpose or "").split())[:400] or _PURPOSE_PLACEHOLDER
    sections = [
        "# Project guide for AI assistants", "",
        "## What this app does", purpose, "",
        "## Stack", *_stack_lines(tc), "",
        "## Commands", *_command_lines(tc), "",
        "## Rules", *_RULES, "",
    ]
    return {"AGENTS.md": "\n".join(sections),
            "CLAUDE.md": "See [AGENTS.md](AGENTS.md) — it is the single source of project rules.\n"}


def check_project_memory(tc: dict[str, str]) -> list[dict]:
    """L0.1 — no rules file for AI assistants."""
    from services.checkup_common import _item
    if has_project_memory(repo_paths(tc)):
        return []
    return [_item(
        scanner="project_memory", rule_id="no_project_memory", severity="medium",
        title="Your AI assistant starts from zero every time and keeps re-making old mistakes",
        impact="Without a short rules file, each AI session guesses your stack and conventions, which causes repeated bugs and wasted time.",
        file="(project root)", line=0,
        detail="no AGENTS.md, CLAUDE.md, .cursorrules or similar rules file found",
        fix_hint="Add an AGENTS.md describing what the app does, the stack, the commands and a few rules. ORA can draft it for your review.")]
