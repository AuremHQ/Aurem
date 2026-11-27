"""
services/seo/sitemap.py — Extract routes from a repo tree and render
a valid sitemap.xml.

We support three router styles:
  - Next.js pages/  router (legacy `pages/*.tsx`)
  - Next.js app/    router (`app/<route>/page.tsx`)
  - Plain static `.html` files (anywhere in repo or under public/)

The caller (orchestrator) hands us a tree from GitHub's recursive
trees API + an optional `public_files_present` flag. We never read
file bytes — pure path-list math.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse
from datetime import datetime, timezone
from typing import Optional

from .meta_tags import SeoPatch


_DYNAMIC = re.compile(r"\[.*?\]")          # Next.js [slug] etc.
_EXT = re.compile(r"\.(tsx|jsx|js|mjs|ts)$")


# Hosts that must never be emitted in a generated sitemap or robots file.
# Fix #20 merge_lastmod already strips these from scanned URLs; the
# renderers below used to fall back to interpolating yourdomain.com
# when no real domain was configured. Tier 3 removes that fallback:
# a missing or placeholder site_url is now an error (sitemap) or omits
# the Sitemap line (robots) instead of emitting a fake domain.
_PLACEHOLDER_HOSTS = ("yourdomain.com", "example.com", "example.org")


def _is_placeholder_site_url(site_url: str | None) -> bool:
    if not site_url or not str(site_url).strip():
        return True
    parsed = urlparse(str(site_url).strip())
    host = (parsed.hostname or str(site_url).strip().split(chr(47))[0] or str()).lower()
    return host in _PLACEHOLDER_HOSTS

# Public alias (Kit 2.0 P0 drift fix): services.seo.robots_txt and
# services.seo.orchestrator import is_placeholder_site_url by this name.
is_placeholder_site_url = _is_placeholder_site_url

def extract_routes(paths: list[str]) -> list[str]:
    """Pure function. Given a flat list of repo paths, return the
    routes a search engine should index. Deduped, sorted, no
    dynamic params (we can't enumerate values for `[slug]` from a
    static scan), no API paths."""
    routes: set[str] = set()

    has_pages = any(p.startswith("pages/") for p in paths)
    has_app   = any(p.startswith("app/") for p in paths)

    for p in paths:
        if _DYNAMIC.search(p):
            continue
        # Next.js pages router
        if has_pages and p.startswith("pages/"):
            if "/api/" in p or p.startswith("pages/api/"):
                continue
            if not _EXT.search(p):
                continue
            inner = p[len("pages/"):]
            inner = _EXT.sub("", inner)
            if inner.split("/")[-1].startswith("_"):
                continue
            if inner.endswith("/index"):
                inner = inner[: -len("/index")]
            if inner == "index" or inner == "":
                routes.add("/")
            else:
                routes.add("/" + inner)
            continue
        # Next.js app router
        if has_app and p.startswith("app/"):
            if not p.endswith(("page.tsx", "page.jsx", "page.js")):
                continue
            inner = p[len("app/"):]
            # Drop the trailing /page.ext segment.
            inner = inner.rsplit("/", 1)[0] if "/" in inner else ""
            # Drop route-group segments like (marketing)/...
            inner = "/".join(
                seg for seg in inner.split("/")
                if not (seg.startswith("(") and seg.endswith(")"))
            )
            routes.add("/" + inner if inner else "/")
            continue
        # Plain HTML files
        if p.endswith(".html"):
            # Strip a leading public/ if present so the route maps
            # to a clean URL.
            rel = p[len("public/"):] if p.startswith("public/") else p
            rel = rel[:-len(".html")]
            if rel.endswith("/index") or rel == "index":
                routes.add("/" if rel == "index" else "/" + rel[:-len("/index")])
            else:
                routes.add("/" + rel)
            continue

    # Always include the root.
    routes.add("/")
    # Dedup + sort, drop the literal "/" duplicates.
    return sorted(routes)


def render_sitemap_xml(
    routes: list[str], site_url: str,
    *, lastmod: Optional[str] = None,
) -> str:
    if _is_placeholder_site_url(site_url):
        raise ValueError("render_sitemap_xml requires a real site_url; refusing to emit placeholder-domain URLs")
    base = str(site_url).rstrip(chr(47))
    date = lastmod or datetime.now(timezone.utc).date().isoformat()
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for r in routes:
        priority = "1.0" if r == "/" else "0.8"
        loc = base + ("" if r == "/" else r)
        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        parts.append(f"    <lastmod>{date}</lastmod>")
        parts.append("    <changefreq>weekly</changefreq>")
        parts.append(f"    <priority>{priority}</priority>")
        parts.append("  </url>")
    parts.append("</urlset>")
    parts.append("")
    return "\n".join(parts)


def patch_sitemap(
    *,
    paths: list[str],
    site_url: str,
    has_public_dir: bool,
    existing_public_sitemap: Optional[str] = None,
    existing_root_sitemap: Optional[str] = None,
    lastmod: Optional[str] = None,
) -> Optional[SeoPatch]:
    if _is_placeholder_site_url(site_url):
        return None
    routes = extract_routes(paths)
    target = "public/sitemap.xml" if has_public_dir else "sitemap.xml"
    existing = (
        existing_public_sitemap if has_public_dir else existing_root_sitemap
    )
    body = render_sitemap_xml(routes, site_url, lastmod=lastmod)
    if (existing or "").strip() == body.strip():
        return None
    return SeoPatch(
        path=target,
        before=existing,
        after=body,
        action="modify" if existing is not None else "create",
        reason=f"generated sitemap.xml ({len(routes)} routes)",
    )

# --- Merged from former visibility kit (sitemap) (Fix #20) ---
# render_sitemap / merge_lastmod keep an existing sitemap.xml intact and
# only refresh lastmod dates; render_sitemap_xml / patch_sitemap above
# generate a sitemap from routes.
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
    def _norm(loc: str) -> str:
        # Trailing-slash-insensitive: a rendered sitemap stores the root
        # as "https://x" while a scan reports "https://x/"; collapse both
        # to one canonical loc so a merge never duplicates the root.
        return loc.rstrip("/") or loc
    fresh = [
        _norm(u["loc"]) for u in urls
        if not any(h in u["loc"] for h in _PLACEHOLDER_HOSTS)
    ]
    merged_locs = list(dict.fromkeys(fresh + [_norm(l) for l in _existing_locs(existing_xml)]))
    return render_sitemap([{"loc": loc} for loc in merged_locs], scan_date)
