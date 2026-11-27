"""
services/seo/robots_txt.py — Create or update robots.txt at
public/robots.txt (preferred) or robots.txt (fallback).
"""
from __future__ import annotations

from typing import Optional

from .meta_tags import SeoPatch
from .sitemap import _is_placeholder_site_url


DEFAULT_DISALLOW = ("/admin", "/api", "/private", "/.env")


def render_robots_txt(
    *, site_url: str, disallow_paths: tuple[str, ...] = DEFAULT_DISALLOW,
) -> str:
    """Pure function — render the canonical robots.txt body."""
    lines = ["User-agent: *", "Allow: /"]
    for p in disallow_paths:
        lines.append(f"Disallow: {p}")
    lines.append("")
    if not _is_placeholder_site_url(site_url):
        lines.append(f"Sitemap: {str(site_url).rstrip(chr(47))}/sitemap.xml")
    return "\n".join(lines) + "\n"


def patch_robots_txt(
    *,
    existing_public_robots: Optional[str],
    existing_root_robots: Optional[str],
    site_url: str,
    has_public_dir: bool,
    disallow_paths: tuple[str, ...] = DEFAULT_DISALLOW,
) -> Optional[SeoPatch]:
    """Decide WHERE to write (public/ or root) and whether to write
    at all (byte-identical content → skip).

    Inputs are pre-fetched by the caller (orchestrator) so this stays
    a pure function and is trivially unit-testable.
    """
    target_path = (
        "public/robots.txt" if has_public_dir else "robots.txt"
    )
    existing = (
        existing_public_robots if has_public_dir else existing_root_robots
    )
    new_body = render_robots_txt(
        site_url=site_url, disallow_paths=disallow_paths,
    )
    if (existing or "").strip() == new_body.strip():
        return None
    return SeoPatch(
        path=target_path,
        before=existing,
        after=new_body,
        action="modify" if existing is not None else "create",
        reason=(
            "created robots.txt"
            if existing is None
            else "updated robots.txt with sitemap + disallows"
        ),
    )
# --- Merged from former visibility kit (robots) (Fix #20) ---
# Managed AI-crawler block: idempotent read-modify-write of an existing
# robots.txt (markers preserved, other lines untouched). Kept alongside
# render_robots_txt / patch_robots_txt above, which generate a whole file.
_START = "# --- AI crawlers (managed by AUREM) ---"
_END = "# --- end AUREM block ---"

RETRIEVAL_BOTS = ["OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"]
TRAINING_BOTS = ["GPTBot", "ClaudeBot", "Google-Extended", "CCBot"]


def render_managed_block(bot_policy: dict) -> str:
    lines = [_START]
    for bot in RETRIEVAL_BOTS:
        lines.append(f"User-agent: {bot}\nAllow: /")
    for bot in TRAINING_BOTS:
        decision = (bot_policy or {}).get(bot, "deny")  # D1 default: DENY
        verb = "Allow" if decision == "allow" else "Disallow"
        lines.append(f"User-agent: {bot}\n{verb}: /")
    lines.append(_END)
    return "\n".join(lines) + "\n"


def apply_managed_block(existing_content: str | None, bot_policy: dict) -> str:
    """R5/R6/R7 — merge the managed block into `existing_content` without
    touching any other line. `existing_content=None` (no robots.txt yet)
    produces a fresh file with just the managed block."""
    block = render_managed_block(bot_policy)
    if not existing_content:
        return block
    if _START in existing_content and _END in existing_content:
        pre, rest = existing_content.split(_START, 1)
        _, post = rest.split(_END, 1)
        return pre.rstrip("\n") + ("\n\n" if pre.strip() else "") + block + post.lstrip("\n")
    # No existing managed block — append ours, preserve everything else.
    return existing_content.rstrip("\n") + ("\n\n" if existing_content.strip() else "") + block
