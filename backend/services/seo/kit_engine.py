"""
services/seo/kit_engine.py — Visibility Kit 2.0 (P0): the ONE engine.

Why this file exists (B2/B3): two engines used to write the same
files with different algorithms — the Kit (managed blocks, PR-only)
and the SEO orchestrator (whole-file regeneration, direct commit to
the base branch). The orchestrator's run after a Kit apply wiped the
Kit's managed robots block, and its direct commit violated the
repo's own ship-via-PR rule. From here on there is one engine and
one ship path:

  build files (managed blocks only) -> ALWAYS create_or_reuse_branch
  + commit_files to that throwaway branch + open_or_reuse_pr + label
  -> never commit_files to the base branch. Direct commit = FAIL.

What lives here (P0):
  P0-1  verify_kit_live() + kit_apply_enabled() — the automated
        live-proof check B4 asked for (fetch live_url, look for an
        AUREM marker). A marker proves a Kit file is live; it is NOT
        by itself proof of a full PR->merge->Live cycle — the webhook
        promotion below is the other half.
  P0-2  run_visibility_kit() — builds every Kit item through the
        managed-block generators, framework-aware, PR-only.
        ship_files_via_pr() — the single ship primitive the
        orchestrator's run_seo_fixes must route through (see EDITS.md).
  P0-3  answer_blocks integration (services/seo/answer_blocks.py).
  P0-4  promote_kit_pr_merged() / promote_kit_pr_live() /
        reconcile_kit_prs() + schedule_kit_merge_cron() — the merge
        webhook's promotion logic (loop_safety.py calls the promote
        helper; the cron is the 6h fallback).

Rules this file enforces, not just documents:
  - robots.txt: ONLY robots_gen.apply_managed_block. The
    orchestrator's patch_robots_txt (whole-file regenerate) is never
    imported or called on this path.
  - schema: ONLY the Kit managed block (schema_gen). The
    orchestrator's patch_schema_markup (BeautifulSoup, skips when ANY
    ld+json exists) is never called on this path.
  - sitemap.xml: ONLY sitemap_gen.merge_lastmod. A fresh sitemap is
    rendered ONLY when no sitemap exists at all; an empty/stale scan
    can never delete live <loc> entries (B7/PR #386 lesson).
  - image alts: patch_image_alts runs ONLY when the caller supplies
    an explicit alt_provider. The Kit forbids filename-derived alt
    text, and patch_image_alts silently falls back to exactly that
    ("image: <filename>") when its LLM fails — so with no provider
    the Kit skips alts and says so, instead of shipping the
    forbidden fallback (B13).
  - dry_run=True makes ZERO GitHub write calls (no branch, no commit,
    no PR) and returns patches + unified diffs.
  - No test is deleted/weakened/skipped by this file, and this file
    never touches the #387 branch/gate or TOKEN_BILLING_MODE.

Unrun-note (2026-11-27): this file was authored as a draft from
read-only inspection of main 791cdb79. It has NOT been executed on
this VM (executing the cloned repo is barred here) — the VS Code
step runs the suite and fixes any import/typo drift before the
draft PR is opened.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

from services.github_api_writer import commit_files, fetch_file, find_html_entry_path
from services.git_identity import resolve_git_identity
from services.loop_safety import (
    add_pr_label,
    create_or_reuse_branch,
    get_pr_status,
    open_or_reuse_pr,
    ship_branch_name,
)
from services.seo import llms_txt as llms_gen
from services.seo import preferred_sources as badge_gen
from services.seo import robots_txt as robots_gen
from services.seo import schema_markup as schema_gen
from services.seo import sitemap as sitemap_gen
from services.seo.answer_blocks import build_answer_blocks
from services.seo.framework_detect import detect_framework
from services.seo.image_alts import patch_image_alts
from services.seo.meta_tags import patch_meta_tags

logger = logging.getLogger(__name__)

# The webhook branch in loop_safety.dispatch_pull_request_webhook
# matches labels starting "auremcto/visibility-kit-" (trailing dash
# is part of the prefix). A label without that prefix is invisible to
# it — that is exactly how Kit PRs used to fall through to the
# log-only path (B5). Keep this value in sync with that prefix.
KIT_PR_LABEL = "auremcto/visibility-kit-apply"

# Items the Kit can apply itself (auto) vs checklist-only (advisory).
# Single source of truth; build_kit_files and the visibility_apply
# compatibility delegate re-export these names.
IMPLEMENTED_AUTO_ITEMS = {
    "ai_crawler_policy", "structured_data", "sitemap_auto",
    "preferred_sources", "llms_txt", "answer_blocks",
}
ADVISORY_ITEMS = {
    "image_quick_wins", "google_business_profile",
    "search_console_gbp_check", "google_platforms_connected",
}

# CSR warning copy — shown in the PR body + result for React/Vite
# CSR sites. B8 nuance: the JSON-LD/badge ARE readable by a non-JS
# fetcher because they sit in the static index.html file itself, so
# this is a warning, not a refusal — the hard failure is only where
# no index.html exists at all (Next.js App Router / WordPress /
# Shopify), which the framework plan below handles by skipping the
# HTML inject instead.
CSR_WARNING = (
    "This site renders client-side (React/Vite): the Kit injects into "
    "the static index.html. Google renders it, but a fetcher that "
    "does not run JavaScript (including some ChatGPT fetches) may not "
    "see content that only exists after JavaScript runs."
)

NEXT_METADATA_SNIPPET = """\
Add to app/layout.tsx `metadata` (copy-paste — the Kit does not edit
source files for Next.js App Router sites, there is no index.html to
inject into):

  export const metadata = {{
    title: "{name}",
    description: "{description}",
  }};
"""

WORDPRESS_MU_PLUGIN_SNIPPET = """\
WordPress site — the Kit does not edit theme files. Add this as a
must-use plugin (wp-content/mu-plugins/aurem-visibility.php) or paste
the JSON-LD into your SEO plugin's custom-schema field:

  <?php
  // AUREM Visibility Kit — JSON-LD for {name}
  add_action('wp_head', function () {{
    echo '<script type="application/ld+json">{{"@context": \
"https://schema.org", "@type": "Organization", "name": "{name}", \
"url": "{url}"}}</script>';
  }});
"""

SHOPIFY_LIQUID_SNIPPET = """\
Shopify site — the Kit does not edit theme files. Paste this into
theme.liquid before </head> (Online Store > Themes > Edit code):

  <script type="application/ld+json">
  {{"@context": "https://schema.org", "@type": "Organization",
    "name": "{name}", "url": "{url}"}}
  </script>
"""


# ── P0-1: live proof + apply gate ──────────────────────────────────

def _live_url_of(project: dict) -> str | None:
    """Best live URL we know for a project, or None."""
    site_meta = project.get("site_meta") or {}
    domain = site_meta.get("domain")
    if domain and not str(domain).startswith("http"):
        domain = "https://" + str(domain).strip("/")
    return (
        project.get("live_url")
        or project.get("preview_url")
        or domain
        or None
    )


async def _default_http_get(url: str) -> tuple[int | None, str]:
    """Fetch a URL; return (status_code, body text). Never raises —
    a failed fetch is (None, ""), so a down site reads as "not live"
    instead of crashing the gate."""
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "aurem-kit-live-check"})
            return r.status_code, r.text or ""
    except Exception as e:                                    # noqa: BLE001
        logger.debug("verify_kit_live fetch failed for %s: %r", url, e)
        return None, ""


async def verify_kit_live(
    project: dict,
    http_get: Optional[Callable[[str], Any]] = None,
) -> dict:
    """P0-1 / B4: is a Visibility Kit file actually live on the site?

    Fetches the live URL (HTML first, then /robots.txt, then
    /llms.txt) and looks for an AUREM marker in each — the schema
    managed-block marker, the Preferred Sources marker, the robots
    managed-block marker, or the llms.txt marker. 200 + marker = live.
    Stops at the first marker hit; a site with no live URL is not
    live by definition (no URL to check).
    """
    get = http_get or _default_http_get
    live_url = _live_url_of(project)
    if not live_url:
        return {"live": False, "marker_found": None, "checked": [],
                "status_code": None, "live_url": None}
    base = str(live_url).rstrip("/")
    checked: list[str] = []
    candidates = [
        (base, (schema_gen._START, badge_gen._START)),
        (base + "/robots.txt", (robots_gen._START,)),
        (base + "/llms.txt", (llms_gen._MARKER,)),
    ]
    for url, markers in candidates:
        checked.append(url)
        status, body = await get(url)
        if status != 200:
            continue
        for marker in markers:
            if marker in (body or ""):
                return {"live": True, "marker_found": marker,
                        "checked": checked, "status_code": status,
                        "live_url": live_url}
    return {"live": False, "marker_found": None, "checked": checked,
            "status_code": None, "live_url": live_url}


async def probe_citation_live(
    project: dict,
    http_get: Optional[Callable[[str], Any]] = None,
) -> dict:
    """Citation probe (catalog item `citation_probe`): is the content
    a citation engine would read actually served live?

    Honest scope: there is no API that reports "an AI cited this
    site". The probe checks the observable precondition — the live
    site serves the Kit's answer section (or any Kit marker) at its
    live URL. Mock the http_get transport in tests; never present a
    probe pass as "an AI cited you".
    """
    proof = await verify_kit_live(project, http_get=http_get)
    return {
        "ok": bool(proof["live"]),
        "live": bool(proof["live"]),
        "marker_found": proof["marker_found"],
        "checked": proof["checked"],
        "live_url": proof["live_url"],
        "note": "Probe checks the live site serves Kit content; it does "
                "not query any AI answer engine.",
    }


async def kit_apply_enabled(
    db,
    *,
    user_id: str,
    tier: str | None = None,
    project: dict | None = None,
    http_get: Optional[Callable[[str], Any]] = None,
) -> dict:
    """P0-1 / B1+B4: may this user apply the Kit right now?

    Decision order (first match wins):
      1. The `kit_apply_enabled` flag is ON (admin flipped it after a
         real PR->merge->Live proof) -> enabled. The flag is the
         manual override and still the only way to enable Apply for a
         user whose project has no live URL to verify against.
      2. The project has a live URL and verify_kit_live() succeeds ->
         enabled, and the proof is recorded in `kit_live_proofs` (an
         audit row — this function NEVER flips the flag itself; flipping
         a kill-switch from a request path would hide the proof step).
      3. No live URL to check, and the caller is founder tier or the
         environment is preview/dev -> enabled (default ON). The first
         apply IS the proof run for these; production without a live
         URL stays OFF.
      4. Otherwise -> disabled, with the same reason string the router
         already returns, so clients see no behaviour change.
    """
    from services.feature_flags import is_enabled as _flag_enabled

    if await _flag_enabled("kit_apply_enabled", user_id=user_id, tier=tier):
        return {"enabled": True, "reason": "flag_on", "live_proof": None}
    if project is not None and _live_url_of(project):
        proof = await verify_kit_live(project, http_get=http_get)
        if proof["live"]:
            if db is not None:
                try:
                    await db.kit_live_proofs.insert_one({
                        "project_id": project.get("project_id"),
                        "user_id": user_id,
                        "live_url": proof["live_url"],
                        "marker_found": proof["marker_found"],
                        "checked": proof["checked"],
                        "verified_at": time.time(),
                    })
                except Exception:                            # noqa: BLE001
                    pass  # proof recording is audit-only; never block Apply
            return {"enabled": True, "reason": "live_proof", "live_proof": proof}
        return {"enabled": False, "reason": "apply_not_proven_live",
                "live_proof": proof}
    env = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "production"))
    if (tier or "").lower() == "founder" or env in ("preview", "development", "dev"):
        return {"enabled": True, "reason": "founder_or_preview_default",
                "live_proof": None}
    return {"enabled": False, "reason": "apply_not_proven_live",
            "live_proof": None}


# ── P0-2: framework-aware planning ─────────────────────────────────

def _extra_framework_from_tree(file_tree: list[str]) -> str | None:
    """Detect the frameworks detect_framework() misses (B9): WordPress
    and Shopify have no package.json deps to key on, but their trees
    are unmistakable. Returns the framework or None."""
    paths = file_tree or []
    if any(p.startswith("wp-content/") or p.startswith("wp-includes/")
           or p in ("wp-config.php", "wp-admin/index.php") for p in paths):
        return "wordpress"
    if any(p.endswith(".liquid") or p == "config/settings_schema.json"
           for p in paths):
        return "shopify"
    return None


def _framework_plan(
    framework: str, unknown_fallback: bool, *, site_name: str,
    site_url: str, description: str,
) -> dict:
    """What the framework means for the HTML inject. Returns:
    html_inject (bool), csr_warning (str|None), notes (list[str]),
    pr_body_extra (str). The hard rule: where no index.html exists
    (Next App Router / WordPress / Shopify) we NEVER invent an HTML
    file to inject into — we ship the file items (robots/sitemap/
    llms/answers.md) and put a copy-paste snippet in the PR body."""
    notes: list[str] = []
    extra = ""
    if framework == "next":
        notes.append(
            "Next.js App Router: no index.html to inject into — JSON-LD "
            "and the Preferred Sources badge are NOT auto-applied. "
            "Copy the metadata snippet from the PR body into "
            "app/layout.tsx instead.")
        extra = NEXT_METADATA_SNIPPET.format(
            name=site_name, description=description or site_name)
        return {"html_inject": False, "csr_warning": None, "notes": notes,
                "pr_body_extra": extra}
    if framework == "wordpress":
        notes.append(
            "WordPress: theme files are not edited — JSON-LD and the "
            "badge are NOT auto-applied. Use the mu-plugin snippet in "
            "the PR body.")
        extra = WORDPRESS_MU_PLUGIN_SNIPPET.format(name=site_name, url=site_url or "")
        return {"html_inject": False, "csr_warning": None, "notes": notes,
                "pr_body_extra": extra}
    if framework == "shopify":
        notes.append(
            "Shopify: theme files are not edited — JSON-LD is NOT "
            "auto-applied. Paste the Liquid snippet from the PR body "
            "into theme.liquid.")
        extra = SHOPIFY_LIQUID_SNIPPET.format(name=site_name, url=site_url or "")
        return {"html_inject": False, "csr_warning": None, "notes": notes,
                "pr_body_extra": extra}
    if framework == "react":
        return {"html_inject": True, "csr_warning": CSR_WARNING,
                "notes": [CSR_WARNING], "pr_body_extra": ""}
    if unknown_fallback:
        notes.append(
            "Framework unknown — treated as static injection into the "
            "found HTML entry file; check the PR diff before merging.")
    return {"html_inject": True, "csr_warning": None, "notes": notes,
            "pr_body_extra": extra}


# ── P0-2: options ──────────────────────────────────────────────────

@dataclass
class KitOptions:
    """Per-run knobs for run_visibility_kit()."""
    dry_run: bool = False
    # Framework-aware handling (P0-2). Turning this off restores the
    # old static-inject behaviour for every framework — only useful
    # for a test that wants to isolate the generators.
    framework_aware: bool = True
    # Optional orchestrator extras, off by default on the Kit path.
    # include_meta needs title/description to do anything.
    # include_alts needs an explicit alt_provider (see module
    # docstring: with no provider the LLM-fallback would write the
    # filename-derived alt the Kit forbids, so alts stay skipped).
    include_meta: bool = False
    include_alts: bool = False
    alt_provider: Optional[object] = field(default=None, repr=False)
    site_url: str = ""
    title: str = ""
    description: str = ""
    og_image: str = ""
    author: str = ""
    commit_message: str = "Visibility Kit — by AUREM"
    force: bool = False


# ── P0-2: file building (managed blocks only) ──────────────────────

def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse
    return urlparse(url).netloc or None


async def _pick_managed_path(
    owner: str, repo: str, base_branch: str, token: str,
    filename: str, file_tree: list[str],
) -> tuple[str, str | None]:
    """Where a root-or-public file (robots.txt / sitemap.xml) lives.
    Prefers public/ when the tree has it, else the side that already
    exists, else the root. Returns (path, existing_content)."""
    has_public = any(p == "public" or p.startswith("public/") for p in file_tree)
    public_path = f"public/{filename}"
    existing_public = await fetch_file(owner, repo, public_path, base_branch, token)
    existing_root = await fetch_file(owner, repo, filename, base_branch, token)
    if has_public or existing_public is not None:
        return public_path, existing_public
    if existing_root is not None:
        return filename, existing_root
    return filename, None


async def build_kit_files(
    *,
    project: dict,
    requested_items: list[str],
    token: str,
    scan_urls: list[dict],
    site_meta: dict,
    bot_policy: dict,
    options: KitOptions,
    file_tree: list[str] | None = None,
) -> dict:
    """Build the Kit's file set WITHOUT writing anything to GitHub.
    Pure-ish: the only I/O is GitHub reads (fetch_file /
    find_html_entry_path / _fetch_tree for framework detection).
    Returns files/conflicts/applied/framework info for both the live
    run and dry_run. `to_apply` is the implemented set; advisory
    items are reported, never written — except answer_blocks, which
    P0-3 promotes from advisory to auto (see below)."""
    from services.repo_context import _fetch_tree

    owner = project.get("github_owner") or project.get("owner")
    repo = project.get("github_repo") or project.get("repo")
    base_branch = project.get("branch") or "main"
    tree = (list(file_tree) if file_tree is not None
            else list(site_meta.get("file_tree") or []))
    if options.framework_aware and file_tree is None and not tree:
        # No tree on file at all — fetch it once for framework
        # detection. An explicitly empty tree ([]) means "no files",
        # not "unknown" — never fetch for that case.
        try:
            fetched, _truncated = await _fetch_tree(owner, repo, base_branch, token)
            tree = [n.get("path", "") for n in (fetched or []) if n.get("path")]
        except Exception:                                    # noqa: BLE001
            tree = []  # framework falls back to unknown + PR note

    framework, unknown_fallback = detect_framework(tree, site_meta.get("package_json"))
    if options.framework_aware:
        extra = _extra_framework_from_tree(tree)
        if extra:
            framework, unknown_fallback = extra, False
    site_name = project.get("name") or repo or "this site"
    _domain = site_meta.get("domain")
    if _domain:
        _domain = str(_domain).strip("/")
        resolved_url = _domain if _domain.startswith("http") else "https://" + _domain
    else:
        resolved_url = (
            project.get("preview_url") or project.get("live_url")
            or options.site_url or ""
        )
    plan = _framework_plan(
        framework if options.framework_aware else "static",
        unknown_fallback, site_name=site_name, site_url=resolved_url,
        description=options.description or site_meta.get("description") or "",
    ) if options.framework_aware else {
        "html_inject": True, "csr_warning": None, "notes": [],
        "pr_body_extra": "",
    }

    implemented = IMPLEMENTED_AUTO_ITEMS
    advisory = ADVISORY_ITEMS
    advisory_skipped = sorted(set(requested_items) & advisory)
    to_apply = sorted(set(requested_items) & implemented)
    files: dict[str, str] = {}
    conflicts: dict[str, str] = {}
    notes: list[str] = list(plan["notes"])
    scan_date = time.strftime("%Y-%m-%d", time.gmtime())

    # Schema dict, resolved from what we already know (B6: nothing
    # ever writes site_meta["schema"], so never trust that field).
    schema = dict(site_meta.get("schema") or {})
    schema.setdefault("name", site_name)
    if resolved_url:
        schema.setdefault("url", resolved_url)
    faqs = list(site_meta.get("faqs") or [])
    if faqs:
        schema["faqs"] = faqs

    # ── robots.txt: ONLY apply_managed_block (B2). ──
    if "ai_crawler_policy" in to_apply:
        robots_path, existing_robots = await _pick_managed_path(
            owner, repo, base_branch, token, "robots.txt", tree)
        files[robots_path] = robots_gen.apply_managed_block(existing_robots, bot_policy)

    # ── Resolve the HTML entry path once (shared by the HTML items). ──
    html_path = "index.html"
    html_probe: str | None = None
    html_needed = bool(
        {"structured_data", "preferred_sources", "answer_blocks"} & set(to_apply)
    ) and plan["html_inject"]
    if html_needed:
        html_path = site_meta.get("html_entry_path") or "index.html"
        html_probe = await fetch_file(owner, repo, html_path, base_branch, token)
        if html_probe is None:
            found = await find_html_entry_path(owner, repo, base_branch, token)
            if found:
                html_path = found
                html_probe = await fetch_file(owner, repo, html_path, base_branch, token)

    if "structured_data" in to_apply:
        if not plan["html_inject"]:
            conflicts["structured_data"] = (
                f"framework {framework}: no HTML entry file to inject into — "
                "see the PR-body snippet instead")
        elif html_probe is None:
            conflicts["structured_data"] = f"{html_path} not found in this repo"
        elif not resolved_url:
            conflicts["structured_data"] = (
                "no site URL/domain on file — cannot build valid JSON-LD")
        elif schema_gen._START not in html_probe and "</head>" not in html_probe \
                and not options.force:
            conflicts["structured_data"] = f"{html_path} has no </head> to inject into"
        else:
            if "</head>" in html_probe and schema_gen._START not in html_probe:
                block = schema_gen.render_json_ld(schema)
                files[html_path] = html_probe.replace("</head>", block + "</head>", 1)
            else:
                files[html_path] = schema_gen.apply_managed_block(html_probe, schema)

    if "sitemap_auto" in to_apply:
        # ONLY merge_lastmod (B2/B7). Regenerate from scratch ONLY
        # when no sitemap exists at all — and only then, only if the
        # scan actually found URLs; an empty scan + no sitemap is a
        # conflict, not an empty sitemap.
        sitemap_path, existing_sitemap = await _pick_managed_path(
            owner, repo, base_branch, token, "sitemap.xml", tree)
        if existing_sitemap is None and not scan_urls:
            conflicts["sitemap_auto"] = (
                "no sitemap.xml in repo and no scanned URLs — nothing to "
                "generate from; run a scan first")
        else:
            files[sitemap_path] = sitemap_gen.merge_lastmod(
                existing_sitemap, scan_urls, scan_date)

    if "preferred_sources" in to_apply:
        if not plan["html_inject"]:
            conflicts["preferred_sources"] = (
                f"framework {framework}: no HTML entry file to inject into — "
                "see the PR-body snippet instead")
        else:
            existing_html_ps = files.get(html_path) or html_probe
            domain = site_meta.get("domain") or _domain_from_url(resolved_url)
            if existing_html_ps is None:
                conflicts["preferred_sources"] = f"{html_path} not found in this repo"
            elif not domain:
                conflicts["preferred_sources"] = (
                    "no site URL/domain on file — cannot build the deeplink")
            elif badge_gen._START not in existing_html_ps \
                    and "</body>" not in existing_html_ps and not options.force:
                conflicts["preferred_sources"] = (
                    f"{html_path} has no </body> to inject into")
            else:
                files[html_path] = badge_gen.apply_managed_block(
                    existing_html_ps, domain, site_name)

    if "llms_txt" in to_apply:
        existing_llms = await fetch_file(owner, repo, "llms.txt", base_branch, token)
        llms_content, llms_full_content, conflict = llms_gen.apply_llms_files(
            existing_llms, site_name, resolved_url, scan_urls)
        if conflict and not options.force:
            conflicts["llms_txt"] = "llms.txt already exists without an AUREM marker"
        else:
            files["llms.txt"] = llms_content
            files["llms-full.txt"] = llms_full_content

    # ── P0-3: answer_blocks (promoted advisory -> auto). ──
    answers_result: dict = {}
    if "answer_blocks" in to_apply:
        answers_result = build_answer_blocks(
            site_name=site_name, site_url=resolved_url,
            description=options.description or site_meta.get("description") or "",
            pricing_note=(site_meta.get("pricing_note") or ""),
            faqs=faqs,
        )
        if answers_result["faqs_used"]:
            files["visibility/answers.md"] = answers_result["markdown"]
            if plan["html_inject"] and html_probe is not None:
                existing_html_ab = files.get(html_path) or html_probe
                from services.seo.answer_blocks import apply_managed_block as _ab_block
                files[html_path] = _ab_block(existing_html_ab, answers_result["html"])
            elif plan["html_inject"]:
                notes.append(
                    "answer_blocks: answers.md shipped standalone — no HTML "
                    "entry file found to inject the <section> into.")
            else:
                notes.append(
                    f"answer_blocks: answers.md shipped standalone — framework "
                    f"{framework} has no HTML inject; see the PR body.")
            if answers_result["skipped"]:
                notes.append(
                    "answer_blocks skipped "
                    f"{len(answers_result['skipped'])} question(s) with no "
                    "real number or source fact — nothing invented.")
        else:
            conflicts["answer_blocks"] = (
                "no usable FAQs: no source FAQs and nothing in the "
                "description/pricing carries a real number to quote")

    # ── Optional orchestrator extras (entry file only, opt-in). ──
    if options.include_meta and (options.title or options.description) \
            and html_probe is not None and plan["html_inject"]:
        current = files.get(html_path) or html_probe
        p = patch_meta_tags(
            path=html_path, html=current, title=options.title,
            description=options.description, og_image=options.og_image,
            url=resolved_url)
        if p:
            files[html_path] = p["after"]
    if options.include_alts and html_probe is not None and plan["html_inject"]:
        if options.alt_provider is None:
            notes.append(
                "image alt: skipped — no alt_provider supplied, and the "
                "Kit never auto-fills alt text from filenames (the "
                "orchestrator's LLM-failure fallback does exactly that).")
        else:
            current = files.get(html_path) or html_probe
            p = await patch_image_alts(
                path=html_path, html=current,
                page_context=options.description, alt_provider=options.alt_provider)
            if p:
                files[html_path] = p["after"]

    applied_names = sorted(set(to_apply) - set(conflicts.keys()))
    return {
        "files": files, "conflicts": conflicts, "applied": applied_names,
        "advisory_skipped": advisory_skipped,
        "framework": framework, "unknown_fallback": unknown_fallback,
        "framework_notes": notes, "csr_warning": plan["csr_warning"],
        "pr_body_extra": plan["pr_body_extra"],
        "html_path": html_path if html_needed else None,
        "answers": answers_result,
    }


def _unified_diff(path: str, before: str | None, after: str) -> str:
    """Unified diff for a dry_run patch (before None = new file)."""
    before_lines = (before or "").splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        before_lines, after_lines,
        fromfile=f"a/{path}" if before is not None else "/dev/null",
        tofile=f"b/{path}",
    ))


# ── P0-2: the single ship primitive (PR-only) ──────────────────────

def _render_pr_body(
    applied: list[str], advisory: list[str], paths: list[str], branch: str,
    *, framework_notes: list[str] | None = None, pr_body_extra: str = "",
    csr_warning: str | None = None, label: str = KIT_PR_LABEL,
) -> str:
    changes = "\n".join(f"- {p} (managed block)" for p in paths)
    advisory_line = (f"Advisory (not applied): {advisory} → see report"
                     if advisory else "")
    notes = ""
    if framework_notes:
        notes = "\n### Framework notes\n" + "\n".join(f"- {n}" for n in framework_notes) + "\n"
    csr = f"\n### CSR warning\n{csr_warning}\n" if csr_warning else ""
    extra = f"\n{pr_body_extra}\n" if pr_body_extra else ""
    csp_note = (
        "\n### Note\nIf this site sends a Content-Security-Policy header, "
        "add `news.google.com` to `script-src` or the Preferred Sources "
        "button script will be blocked (the deeplink fallback link still "
        "works either way).\n"
        if "preferred_sources" in applied else ""
    )
    return (
        f"## Visibility Kit ({', '.join(applied)}) — by AUREM\n"
        f"Adds: {applied}\n"
        f"{advisory_line}\n"
        f"### Changes\n{changes}\n"
        f"{notes}{csr}{extra}{csp_note}"
        f"### Revert\ngh branch delete {branch}\n"
        f"_Created by AUREM Visibility Kit. No user copy was modified. "
        f"Training-bot choices per settings. Label: `{label}` — removing "
        f"it breaks merge tracking._\n"
    )


async def ship_files_via_pr(
    db,
    *,
    project: dict,
    token: str,
    files: dict[str, str],
    applied_names: list[str],
    advisory_skipped: list[str] | None = None,
    framework: str = "static",
    unknown_fallback: bool = False,
    framework_notes: list[str] | None = None,
    pr_body_extra: str = "",
    csr_warning: str | None = None,
    commit_message: str | None = None,
) -> dict:
    """Ship an already-built file set as a draft PR. THE only ship
    path for Kit + orchestrator fixes: throwaway branch -> commit ->
    draft PR -> label. Never commits to the project's base branch;
    callers pass only file contents, never a branch name to write to.
    Label attach is best-effort (a label failure never blocks a ship
    that already landed a commit + PR) — but it is attempted on both
    new AND reused PRs, so pre-2.0 PRs gain the label on re-apply.
    Returns the same shape visibility_apply.apply_visibility_kit
    returned, plus label_ok / reused."""
    owner = project.get("github_owner") or project.get("owner")
    repo = project.get("github_repo") or project.get("repo")
    base_branch = project.get("branch") or "main"
    project_id = project["project_id"]
    user_id = project["user_id"]
    advisory_skipped = advisory_skipped or []

    branch = ship_branch_name(f"visibility-kit-{project_id}")
    ok, err = await create_or_reuse_branch(
        owner=owner, repo=repo, base_branch=base_branch, new_branch=branch, token=token)
    if not ok:
        return {"ok": False, "error": f"branch_create_failed_{err}"}

    author_name, author_email = await resolve_git_identity(db, user_id)
    msg = commit_message or f"Visibility Kit ({', '.join(applied_names)}) — by AUREM"
    commit = await commit_files(
        owner, repo, branch, token, files, msg,
        author_name=author_name, author_email=author_email)

    body = _render_pr_body(
        applied_names, advisory_skipped, sorted(files.keys()), branch,
        framework_notes=framework_notes, pr_body_extra=pr_body_extra,
        csr_warning=csr_warning)
    pr_result = await open_or_reuse_pr(
        owner=owner, repo=repo, head_branch=branch, base_branch=base_branch,
        title=f"Visibility Kit ({', '.join(applied_names)}) — by AUREM",
        body=body, token=token,
        update_comment=(
            f"**AUREM Visibility Kit — updated.** New commit re-applies "
            f"{', '.join(applied_names)}.\n\n"
            f"_Reusing this PR instead of opening a duplicate — same "
            f"project, still open._"))
    pr_url = pr_result.get("pr_url")
    if not pr_url:
        return {"ok": False, "error": f"pr_open_failed_{pr_result.get('error')}",
                "branch": branch}
    pr_number = pr_result.get("pr_number")

    # P0-4: label at creation (and on reuse) so the webhook's kit
    # branch can route the PR. Best-effort by design.
    label_ok, label_err = await add_pr_label(
        owner=owner, repo=repo, pr_number=pr_number, label=KIT_PR_LABEL, token=token)
    if not label_ok:
        logger.warning(
            "kit ship: label %s failed on %s/%s#%s: %s (merge tracking "
            "will miss this PR until the label is added manually)",
            KIT_PR_LABEL, owner, repo, pr_number, label_err)

    now = time.time()
    for item_key in applied_names:
        await db.visibility_state.update_one(
            {"project_id": project_id, "item_id": item_key},
            {"$set": {
                "status": "pr_created", "detected_framework": framework,
                "detail": {"pr_url": pr_url, "branch": branch},
                "updated_at": now}},
            upsert=True)
    await db.visibility_applications.insert_one({
        "project_id": project_id, "branch": branch, "pr_number": pr_number,
        "pr_url": pr_url, "items": applied_names, "status": "open",
        "label": KIT_PR_LABEL, "created_at": now})

    return {
        "ok": True, "branch": branch, "pr_url": pr_url, "pr_number": pr_number,
        "reused": bool(pr_result.get("reused")), "label_ok": bool(label_ok),
        "label_error": label_err, "items_covered": applied_names,
        # The branch commit SHA — orchestrator callers (E-4b) keep
        # reporting a commit SHA, now the throwaway-branch one.
        "commit_sha": (commit or {}).get("sha"),
        "commit_url": (commit or {}).get("html_url"),
    }


async def run_visibility_kit(
    db,
    *,
    project: dict,
    requested_items: list[str],
    token: str,
    options: KitOptions | None = None,
    scan_urls: list[dict] | None = None,
    site_meta: dict | None = None,
    bot_policy: dict | None = None,
    file_tree: list[str] | None = None,
) -> dict:
    """P0-2: run the Visibility Kit for one project. Builds files via
    build_kit_files(), then either returns patches + unified diffs
    (dry_run — zero GitHub writes) or ships them via
    ship_files_via_pr(). Never raises for an empty request — an empty
    build returns ok=False with the reason, same as the old apply."""
    opts = options or KitOptions()
    site_meta = site_meta or {}
    built = await build_kit_files(
        project=project, requested_items=requested_items, token=token,
        scan_urls=scan_urls or [], site_meta=site_meta,
        bot_policy=bot_policy or {}, options=opts, file_tree=file_tree)
    files = built["files"]
    if not files:
        return {"ok": False, "error": "all_conflicted",
                "conflicts": list(built["conflicts"].values()),
                "advisory_skipped": built["advisory_skipped"],
                "not_implemented": [],
                "detected_framework": built["framework"],
                "unknown_framework_fallback": built["unknown_fallback"]}

    if opts.dry_run:
        patches = []
        diffs: dict[str, str] = {}
        for path, after in sorted(files.items()):
            patches.append({"path": path, "action": "modify",
                            "reason": "kit dry_run preview",
                            "before_len": None, "after_len": len(after)})
            diffs[path] = _unified_diff(path, None, after)
        return {
            "ok": True, "dry_run": True, "committed": False,
            "patches": patches, "diffs": diffs,
            "items_covered": built["applied"],
            "advisory_skipped": built["advisory_skipped"],
            "not_implemented": [],
            "conflicts": list(built["conflicts"].values()),
            "detected_framework": built["framework"],
            "unknown_framework_fallback": built["unknown_fallback"],
            "framework_notes": built["framework_notes"],
            "csr_warning": built["csr_warning"],
        }

    shipped = await ship_files_via_pr(
        db, project=project, token=token, files=files,
        applied_names=built["applied"],
        advisory_skipped=built["advisory_skipped"],
        framework=built["framework"],
        unknown_fallback=built["unknown_fallback"],
        framework_notes=built["framework_notes"],
        pr_body_extra=built["pr_body_extra"],
        csr_warning=built["csr_warning"],
        commit_message=opts.commit_message)
    if not shipped.get("ok"):
        shipped.setdefault("advisory_skipped", built["advisory_skipped"])
        shipped.setdefault("not_implemented", [])
        shipped.setdefault("conflicts", list(built["conflicts"].values()))
        shipped.setdefault("detected_framework", built["framework"])
        shipped.setdefault(
            "unknown_framework_fallback", built["unknown_fallback"])
        return shipped
    return {
        **shipped,
        "items_covered": built["applied"],
        "advisory_skipped": built["advisory_skipped"],
        "not_implemented": [],
        "conflicts": list(built["conflicts"].values()),
        "detected_framework": built["framework"],
        "unknown_framework_fallback": built["unknown_fallback"],
        "framework_notes": built["framework_notes"],
        "csr_warning": built["csr_warning"],
    }


# ── P0-4: merge promotion (webhook + cron share this) ──────────────

async def promote_kit_pr_merged(
    db, *, pr_number: int, branch: str,
    merge_commit_sha: str | None = None,
) -> dict:
    """Promote a Kit application's items pr_created -> pr_merged.
    The webhook (loop_safety.py kit branch) and the 6h fallback cron
    both call this — one function, so the two paths can never drift.
    Matches on (pr_number, branch): pr_number alone can collide
    across repos. Only pr_created rows move; already-live rows are
    left alone. Returns counts for logging/tests."""
    app = await db.visibility_applications.find_one(
        {"pr_number": pr_number, "branch": branch})
    if not app:
        return {"promoted": 0, "project_id": None, "reason": "no_application"}
    now = time.time()
    res = await db.visibility_state.update_many(
        {"project_id": app["project_id"],
         "item_id": {"$in": app.get("items", [])},
         "status": "pr_created"},
        {"$set": {"status": "pr_merged",
                  "detail.merge_commit_sha": merge_commit_sha,
                  "updated_at": now}})
    await db.visibility_applications.update_one(
        {"_id": app["_id"]},
        {"$set": {"status": "merged", "merged_at": now,
                  "merge_commit_sha": merge_commit_sha}})
    matched = getattr(res, "modified_count", 0) or 0
    return {"promoted": matched, "project_id": app["project_id"],
            "items": app.get("items", [])}


async def promote_kit_pr_live(
    db, *, project: dict,
    http_get: Optional[Callable[[str], Any]] = None,
) -> dict:
    """pr_merged -> live, only once verify_kit_live() sees a Kit
    marker on the live site. A merge without a live marker stays
    pr_merged (deploy may lag the merge) — the score already pays
    full weight for pr_merged, so nothing is lost by waiting."""
    proof = await verify_kit_live(project, http_get=http_get)
    if not proof["live"]:
        return {"promoted": 0, "live": False, "project_id": project.get("project_id")}
    now = time.time()
    res = await db.visibility_state.update_many(
        {"project_id": project.get("project_id"), "status": "pr_merged"},
        {"$set": {"status": "live", "updated_at": now}})
    matched = getattr(res, "modified_count", 0) or 0
    return {"promoted": matched, "live": True,
            "project_id": project.get("project_id")}


async def reconcile_kit_prs(
    db, *, http_get: Optional[Callable[[str], Any]] = None,
) -> dict:
    """6h fallback cron body: for every still-open Kit application,
    ask GitHub whether its PR merged (get_pr_status); promote via
    the same helpers the webhook uses, then try pr_merged -> live.
    A get_pr_status failure (ok=False) is 'could not verify', never
    'unmerged' — the row is left alone for the next pass."""
    from services.pat_vault import get_repo_token_or_error

    checked = promoted_merged = promoted_live = 0
    errors: list[str] = []
    apps = await db.visibility_applications.find(
        {"status": "open"}).to_list(500)
    for app in apps:
        checked += 1
        pr_number = app.get("pr_number")
        project_id = app.get("project_id")
        try:
            proj = await db.cto_projects.find_one({"project_id": project_id})
            if not proj:
                continue
            owner = proj.get("github_owner") or proj.get("owner")
            repo = proj.get("github_repo") or proj.get("repo")
            token, _err, _detail = await get_repo_token_or_error(proj)
            if not token:
                errors.append(f"{project_id}: no GitHub token")
                continue
            status = await get_pr_status(
                owner=owner, repo=repo, pr_number=pr_number, token=token)
            if not status.get("ok"):
                continue  # could not verify — leave for the next pass
            if status.get("merged") or status.get("state") == "closed":
                if status.get("merged"):
                    res = await promote_kit_pr_merged(
                        db, pr_number=pr_number, branch=app.get("branch"),
                        merge_commit_sha=status.get("merge_commit_sha"))
                    promoted_merged += res["promoted"]
                    live = await promote_kit_pr_live(
                        db, project=proj, http_get=http_get)
                    promoted_live += live["promoted"]
                else:
                    await db.visibility_applications.update_one(
                        {"_id": app["_id"]},
                        {"$set": {"status": "closed_unmerged",
                                  "updated_at": time.time()}})
        except Exception as e:                                # noqa: BLE001
            errors.append(f"{project_id}#{pr_number}: {e!r}")
    return {"ok": True, "checked": checked,
            "promoted_merged": promoted_merged,
            "promoted_live": promoted_live, "errors": errors}


async def schedule_kit_merge_cron(db_getter, interval_s: int = 21600) -> None:
    """Background cron — 6h Kit PR reconciliation (same supervised
    shape as schedule_bot_pr_reaper_cron: boot stagger + PID-seeded
    jitter so a 2-worker pod does not run in lockstep). Wired in
    lifespan next to the other crons (EDITS.md, main.py anchor)."""
    from services.integration_health_cron import _startup_jitter_s

    await asyncio.sleep(420 + _startup_jitter_s())
    while True:
        try:
            db = db_getter()
            if db is not None:
                result = await reconcile_kit_prs(db)
                logger.info(
                    "[kit-merge-cron] checked=%s promoted_merged=%s "
                    "promoted_live=%s errors=%s",
                    result["checked"], result["promoted_merged"],
                    result["promoted_live"], len(result["errors"]))
        except Exception as e:                                # noqa: BLE001
            logger.warning("[kit-merge-cron] pass failed: %r", e)
        await asyncio.sleep(interval_s)
