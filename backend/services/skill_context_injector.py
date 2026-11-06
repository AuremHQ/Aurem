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
from typing import List, Optional

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
def _load_skill(filename: str, char_cap: int) -> str:
    """Disk-load a skill file once, then memoise. Returns sliced content."""
    path = os.path.abspath(os.path.join(_SKILLS_DIR, filename))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if len(text) > char_cap:
            text = text[:char_cap] + "\n…(truncated)"
        return text
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


# Security audit (Nov 2026) — the per-skill caps above cut each file from its
# START, so sections that sit deeper never reached the model: Row Level
# Security + Content Security Policy (security-review), Resource Ownership
# (auth-implementation), Clickjacking + Subresource Integrity
# (frontend-security) and the SQL-injection fix (api-security). For
# auth/api/frontend tasks those sections are now sliced BY HEADING and
# appended, each capped, with a hard total cap. Non-security tasks match no
# trigger and keep byte-identical output.
_SECTION_CAP = 1200
_SECTIONS_TOTAL_CAP = 6000
_TRUNCATED = "\n…(truncated)"
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")


def _keywords_for(filename: str) -> list[str]:
    for triggers, name, _cap in _INJECTION_RULES:
        if name == filename:
            return list(triggers)
    return []


_AUTH_KW = _keywords_for("auth-implementation.md")
_API_KW = _keywords_for("api-security.md")
_FRONTEND_KW = _keywords_for("frontend-security.md")
_SECURITY_KW = _AUTH_KW + _API_KW + _FRONTEND_KW

# (trigger keywords, skill file, exact heading line). Order = injection order.
_SECTION_RULES: list[tuple[list[str], str, str]] = [
    (_SECURITY_KW, "security-review.md", "#### Row Level Security (Supabase)"),
    (_SECURITY_KW, "security-review.md", "#### Content Security Policy"),
    (_AUTH_KW, "auth-implementation.md", "### Pattern 3: Resource Ownership"),
    (_FRONTEND_KW, "frontend-security.md", "### Clickjacking Protection"),
    (_FRONTEND_KW, "frontend-security.md", "### Browser Security Features"),  # holds the SRI bullet
    (_API_KW, "api-security.md", "#### 1. Use Parameterized Queries"),
]


def _section_label(filename: str, heading: str) -> str:
    return f"{filename.replace('.md', '').upper()}: {heading.lstrip('#').strip()}"


def _extract_section(text: Optional[str], heading: Optional[str], cap: int) -> str:
    """Slice `heading` .. next heading of the same or higher level.
    Headings inside ``` code fences are ignored (a `# comment` in a code block
    is not a heading). Returns "" when the heading is missing. The result,
    truncation marker included, never exceeds `cap` and is cut at a line end."""
    if not isinstance(text, str) or not isinstance(heading, str) or not heading.strip().startswith("#"):
        return ""
    wanted = heading.strip()
    wanted_level = len(wanted) - len(wanted.lstrip("#"))
    lines = text.splitlines()
    in_fence = False
    start = None
    start_fence = False
    end = len(lines)
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if start is not None and start_fence and not in_fence:
                end = i  # the fence that held the section just closed
                break
            continue
        if start is None:
            if line.strip() == wanted:
                start, start_fence = i, in_fence
            continue
        m = _HEADING_RE.match(line)
        if m and in_fence == start_fence and len(m.group(1)) <= wanted_level:
            end = i
            break
    if start is None:
        return ""
    body = "\n".join(lines[start:end]).rstrip()
    if len(body) > cap:
        room = max(cap - len(_TRUNCATED), 0)
        cut = body[:room]
        if "\n" in cut:
            cut = cut[: cut.rindex("\n")]
        body = cut.rstrip() + _TRUNCATED
    return body


@lru_cache(maxsize=32)
def _load_section(filename: str, heading: str, cap: int) -> str:
    path = os.path.abspath(os.path.join(_SKILLS_DIR, filename))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _extract_section(fh.read(), heading, cap)
    except OSError:
        return ""


def select_security_sections(task_description: Optional[str], already_injected: tuple = ()) -> List[tuple[str, str]]:
    """(label, body) for each heading-sliced section whose trigger matches the
    task. Empty for non-security tasks. Filenames and headings come from
    _SECTION_RULES only; the task text is only ever matched against keywords."""
    task = (task_description or "").lower() if isinstance(task_description, str) else ""
    if not task:
        return []
    picked: list[tuple[str, str]] = []
    used = 0
    for triggers, filename, heading in _SECTION_RULES:
        if not any(kw in task for kw in triggers):
            continue
        body = _load_section(filename, heading, _SECTION_CAP)
        if not body:
            continue
        # Already visible through the start-of-file slice: do not repeat it.
        if any(body[:300] in injected for injected in already_injected):
            continue
        room = _SECTIONS_TOTAL_CAP - used
        if len(body) > room:
            if room < 200:
                continue
            body = _extract_section(body, heading, room)
            if not body:
                continue
        picked.append((_section_label(filename, heading), body))
        used += len(body)
    return picked


def select_skills(task_description: str) -> List[tuple[str, str]]:
    """Returns list of (skill_name, sliced_content) to inject."""
    task = (task_description or "").lower()
    picked: list[tuple[str, str]] = []
    seen_files: set[str] = set()
    for triggers, filename, cap in _INJECTION_RULES:
        if filename in seen_files:
            continue
        if any(kw in task for kw in triggers):
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
    sections = select_security_sections(task_description, tuple(c for _, c in picks))
    if sections:
        parts.append("\n[VANGUARD SECURITY SECTIONS — targeted, apply when relevant]")
        for label, body in sections:
            parts.append(f"\n### {label}\n{body}")
    return "\n".join(parts)
