"""
backend/scripts/seed_visibility_items.py — Visibility Kit 2.0 (P0-5).

Standalone, idempotent seed for the `visibility_items` catalog.

Run:  python -m backend.scripts.seed_visibility_items [--dry-run]

Reconciles with migrations/003_visibility_kit.py (the 10 items that
seed already exists — VERIFY.md B14 REFUTED the "no seed" claim):
  - answer_blocks: advisory weight 7 -> auto weight 15 (P0-3: GEO
    apply now writes the passages instead of listing gaps).
  - product_feed_acp: NEW, weight 12, auto, ecommerce-only (a
    product feed / ACP manifest only exists on a shop; the engine
    must skip it, not fail, on a non-shop — see kit_engine's
    `implemented` set, which does not include it until the feed
    generator lands in P1).
  - citation_probe: NEW, weight 0, advisory — a measurement, not a
    fix. Weight 0 (same deliberate choice as google_business_profile)
    so a probe row never earns and never dilutes the score.
  - Everything else keeps migration 003's weights and copy verbatim.
    Total weight moves 100 -> 120; the existing test_catalog_seeded
    assertion (sum == 100) must be updated to 120 as part of this
    catalog change (EDITS.md E-14 — a spec change, not a weakening).

Wiring: this script is NOT called from lifespan today (verified:
backend/main.py has no migration/seed call; the migration runs via
`python -m backend.migrations`). EDITS.md wires it into the
lifespan boot block next to init_prod_collections (main.py:1551) so
a fresh DB is seeded on boot; until then the router returns 503
"run seed" on an empty catalog instead of a silent score of 0.

Unrun-note (2026-11-27): authored as a draft from read-only
inspection of main 791cdb79; not executed on this VM. The VS Code
step runs it with --dry-run first, then for real, before the suite.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# 12-item catalog — the single source of truth for copy + weights.
# Rows 1-10 reconcile with migrations/003_visibility_kit.py; rows
# 11-12 are the P0 additions. Keep `key` stable: it is the join key
# for visibility_state rows and the score denominator.
CATALOG_ITEMS_12: list[dict] = [
    {
        "key": "preferred_sources", "weight": 25, "mode": "auto",
        "name": "Preferred Sources badge",
        "what_why": (
            "Your visitors can mark you a 'preferred source' on Google — a "
            "PER-VISITOR choice, not a global ranking signal. Once they do, "
            "they see a 'preferred' badge on your links in AI Mode, AI "
            "Overviews and Top Stories. Google reports preferred links get "
            "about 2x the clicks (May 2026). ~2 minutes to install."
        ),
        "frameworks": ["next", "react", "static"], "sort": 1,
    },
    {
        "key": "ai_crawler_policy", "weight": 20, "mode": "auto",
        "name": "robots.txt AI policy",
        "what_why": (
            "Tell ChatGPT, Claude & Perplexity exactly which of their bots "
            "may read your site — so AI answers are grounded in your "
            "content, and you control who trains on it."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 2,
    },
    {
        "key": "structured_data", "weight": 20, "mode": "auto",
        "name": "JSON-LD + meta/OG",
        "what_why": (
            "Machine-readable facts about your business, pages, and authors "
            "— plus the meta/OG tags answer engines read when deciding what "
            "to quote."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 3,
    },
    {
        "key": "llms_txt", "weight": 15, "mode": "auto",
        "name": "/llms.txt + /llms-full.txt",
        "what_why": (
            "A map of your site that helps Claude, ChatGPT and coding "
            "agents find you. Google ignores it for Search and AI "
            "Overviews. Cheap to add, low-risk, useful for the assistants "
            "that do read it."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 4,
    },
    {
        "key": "sitemap_auto", "weight": 10, "mode": "auto",
        "name": "sitemap.xml generate / lastmod / dupe cleanup",
        "what_why": "Deterministic, 0 tokens — fast 'Live' win.",
        "frameworks": ["next", "react", "static", "unknown"], "sort": 5,
    },
    {
        # P0-3: advisory (weight 7) -> auto (weight 15). The Kit now
        # writes the answer-first passages; the old "you decide" copy
        # is replaced, not kept alongside.
        "key": "answer_blocks", "weight": 15, "mode": "auto",
        "name": "Answer-first passages",
        "what_why": (
            "Pages that answer a question directly in the first 40–60 words "
            "under a question-style heading are the ones AI cites. The Kit "
            "writes one <section id=\"ai-answers\"> per site from your own "
            "description and pricing — every answer carries a real number, "
            "and anything without a source fact is skipped, never invented."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 6,
    },
    {
        "key": "image_quick_wins", "weight": 3, "mode": "advisory",
        "name": "Image quick wins",
        "what_why": (
            "Missing alt / missing lazy-loading / heavy hero images — file "
            "list + advice. Auto alt-fill from filenames is forbidden."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 7,
    },
    {
        "key": "google_business_profile", "weight": 0, "mode": "advisory",
        "name": "Google Business Profile",
        "what_why": (
            "Your Google Business Profile is a citation source for local AI "
            "answers. Complete it yourself in Google's own dashboard — we "
            "don't post on your behalf (GBP's API requires per-client OAuth "
            "+ your own manual sign-in per action, so no SaaS can bulk-"
            "automate this — Google's own policy). Checklist: 5+ photos, "
            "every category/service filled in, NAP (name/address/phone) "
            "matching everywhere online, hours kept current, a weekly post "
            "or photo, and a refresh within 30 days — Whitespark's 2026 "
            "report ties stale profiles to falling impressions."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 8,
    },
    {
        "key": "search_console_gbp_check", "weight": 0, "mode": "advisory",
        "name": "Google Search + Business Profile",
        "what_why": (
            "The Preferred Sources button works best when your site is "
            "verified in Google Search Console and your Business Profile "
            "is fresh. We can't connect these for you (they're Google's, "
            "not ours) — but here's the 3-step checklist: 1) Verify your "
            "site in Google Search Console (search.google.com/search-"
            "console) — 5 min one-time. 2) Claim + complete your Google "
            "Business Profile (business.google.com) — 5+ photos, all "
            "categories, hours up to date. 3) Keep it fresh — profiles "
            "inactive 30+ days can drop in impressions. Do all 3 and the "
            "Preferred Sources badge + AI Overviews both work at full "
            "strength."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 9,
    },
    {
        "key": "google_platforms_connected", "weight": 0, "mode": "advisory",
        "name": "Google Platforms",
        "what_why": (
            "The more your business is connected across Google's products, "
            "the more Google trusts your content in AI answers. This is a "
            "one-time 10-min setup per platform: Search Console "
            "(search.google.com/search-console) — verify your site, get "
            "search insights; Google Business Profile (business.google.com) "
            "— local listings, reviews; Google Analytics "
            "(analytics.google.com) — traffic + behavior data. Connect all "
            "three and your site signals 'trusted source' to every AI "
            "system that reads Google's data on you."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 10,
    },
    {
        # NEW (P0-5). Auto only on a shop: `condition` is read by the
        # engine, which skips this item (not a failure) when the
        # project is not ecommerce. No feed generator ships in P0 —
        # the item is seeded now so the catalog is complete; the
        # generator lands in P1 and the engine must not claim
        # pr_created for it before then.
        "key": "product_feed_acp", "weight": 12, "mode": "auto",
        "condition": "ecommerce_only",
        "name": "Product feed (ACP)",
        "what_why": (
            "A machine-readable product feed so AI shopping answers can "
            "quote your real products, prices, and availability. Only "
            "applies to shops — skipped, not failed, on a non-shop site."
        ),
        "frameworks": ["next", "react", "static", "shopify", "wordpress"],
        "sort": 11,
    },
    {
        # NEW (P0-5). A measurement, not a fix: the probe checks the
        # live site serves Kit content a citation engine can read.
        # Weight 0 by the same deliberate choice as the GBP rows — it
        # never earns and never dilutes the score denominator.
        "key": "citation_probe", "weight": 0, "mode": "advisory",
        "name": "Citation probe (live check)",
        "what_why": (
            "A live check that your site actually serves the Kit's "
            "answer content where a citation engine can read it. The "
            "probe reports what the live site serves — it never claims "
            "an AI cited you."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 12,
    },
]

TOTAL_WEIGHT = sum(item["weight"] for item in CATALOG_ITEMS_12)  # 120


async def seed_visibility_items(db, *, dry_run: bool = False) -> dict:
    """Upsert all 12 catalog rows by `key` (same shape as migration
    003's up()). Safe to re-run: re-running after a copy/weight edit
    updates rows, never duplicates. Returns counts; dry_run writes
    nothing. Also ensures the unique `key` index."""
    now = time.time()
    upserted = 0
    if dry_run:
        return {"ok": True, "dry_run": True, "upserted": len(CATALOG_ITEMS_12),
                "total_weight": TOTAL_WEIGHT, "items": [i["key"] for i in CATALOG_ITEMS_12]}
    for item in CATALOG_ITEMS_12:
        await db.visibility_items.update_one(
            {"key": item["key"]},
            {"$set": {**item, "updated_at": now},
             "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        upserted += 1
    await db.visibility_items.create_index("key", unique=True)
    return {"ok": True, "dry_run": False, "upserted": upserted,
            "total_weight": TOTAL_WEIGHT,
            "items": [i["key"] for i in CATALOG_ITEMS_12]}


async def _main_async(dry_run: bool) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("MONGO_URL and DB_NAME must both be set", file=sys.stderr)
        return 2
    client = AsyncIOMotorClient(mongo_url)
    try:
        result = await seed_visibility_items(client[db_name], dry_run=dry_run)
    finally:
        client.close()
    print(f"visibility catalog seed: {result['upserted']} items "
          f"(total weight {result['total_weight']})"
          f"{' [dry-run, nothing written]' if dry_run else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the Visibility Kit catalog (12 items, idempotent).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be seeded; write nothing.")
    args = parser.parse_args(argv)
    return asyncio.run(_main_async(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
