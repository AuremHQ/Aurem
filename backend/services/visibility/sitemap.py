"""
services/visibility/sitemap.py — sitemap.xml generate/update (spec §5/§6.5).

Deterministic, 0 LLM tokens (R9/R15). Missing → generate from scanned
URLs. Exists → update lastmod, drop dupes (R6 idempotent).
"""
from __future__ import annotations

import re

_AI_COMMENT = "<!-- AI crawlers: see robots.txt (managed by AUREM) -->"

# 2026-09-12 — real, live-production bug (PR #386, TJSNDHU/Aurem):
# `scan_urls` on a project can be empty or stale/placeholder (e.g.
# "yourdomain.com" demo entries) long after the real site has grown.
# merge_lastmod() used to IGNORE existing_xml entirely and rebuild
# purely from `urls`, so a stale/empty scan silently WIPED all 234
# real <loc> entries down to the placeholder-only set. Filter out
# known placeholder hosts from the freshly-scanned set so a bad scan
# can't inject junk into an otherwise-real sitemap.
_PLACEHOLDER_HOSTS = ("yourdomain.com", "example.com", "example.org")


def render_sitemap(urls: list[dict], scan_date: str) -> str:
    """`urls` = [{loc}], deduped + sorted for determinism (R6)."""
    seen = []
    for u in urls:
        loc = u["loc"]
        if loc not in seen:
            seen.append(loc)
    entries = "\n".join(
        f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{scan_date}</lastmod>\n  </url>"
        for loc in sorted(seen)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"{_AI_COMMENT}\n"
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


def _existing_locs(existing_xml: str | None) -> list[str]:
    if not existing_xml:
        return []
    return re.findall(r"<loc>\s*(.*?)\s*</loc>", existing_xml)


def merge_lastmod(existing_xml: str | None, urls: list[dict], scan_date: str) -> str:
    """t_sitemap_idempotent — running apply twice yields one sitemap, no
    dupes, existing entries get a fresh lastmod. GENUINELY merges: the
    real current sitemap's <loc> entries are always preserved, and any
    freshly-scanned URLs are added on top (never the other way around
    — a stale/empty scan must never be able to delete real, live
    entries someone else already has indexed)."""
    fresh = [
        u["loc"] for u in urls
        if not any(h in u["loc"] for h in _PLACEHOLDER_HOSTS)
    ]
    merged_locs = list(dict.fromkeys(fresh + _existing_locs(existing_xml)))
    return render_sitemap([{"loc": loc} for loc in merged_locs], scan_date)
