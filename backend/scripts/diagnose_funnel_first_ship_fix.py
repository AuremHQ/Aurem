"""
scripts/diagnose_funnel_first_ship_fix.py — READ-ONLY comparison.

Founder asked (2026-09-08): "build the read-only diagnostic script,
don't send me a URI... I'll run it myself." This script makes ZERO
writes to any collection — `db.<collection>.find`/`.count_documents`
only, nothing else, exactly like scripts/audit_stuck_github_installs.py.

What it compares:
  OLD logic (frozen copy, exactly what `classify_users()` used to do
  before the 2026-09-08 fix) — excludes any user with `first_chat_at`
  set from EVERY stage bucket, even if they never shipped a real task.
  NEW logic — the real `services/funnel_nudge_cron.classify_users()`
  as it exists in this checkout right now — excludes only users with
  `first_ship_at` set (a real completed loop task).

Prints:
  1. Stage-by-stage count comparison (old vs new).
  2. The exact list of users who are NEWLY visible under the fix —
     i.e. they chatted at least once but never shipped, and were
     previously silently dropped from every stage bucket. This is
     the population the old logic was hiding from the nudge cron.

Usage:
    python scripts/diagnose_funnel_first_ship_fix.py

Reads MONGO_URL/DB_NAME from the environment exactly like the app
does — run it against whichever DB you want to check (this sandbox
or, with your own production MONGO_URL exported in your own shell,
production). No CLI flag exists to make this script write anything.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.funnel_nudge_cron import STAGES, classify_users  # noqa: E402

STUCK_HOURS = 24
_HOUR = 3600


async def _classify_old(db) -> dict[str, list[dict]]:
    """Frozen copy of the pre-fix classify_users() — first_chat_at
    exclusion, kept ONLY here for comparison. Not used by the app."""
    import time

    now = time.time()
    cutoff = now - STUCK_HOURS * _HOUR
    candidates = await db.dev_users.find(
        {"email": {"$exists": True, "$ne": ""}},
        {"_id": 0, "user_id": 1, "email": 1, "name": 1, "created_at": 1,
         "first_chat_at": 1, "is_admin": 1, "is_unlimited": 1, "tier": 1,
         "github": 1},
    ).to_list(length=10_000)

    ids = [u["user_id"] for u in candidates if u.get("user_id")]
    projects_cur = db.cto_projects.find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1})
    users_with_project = {p["user_id"] async for p in projects_cur if p.get("user_id")}

    installs_cur = db.github_installations.find(
        {"user_id": {"$in": ids}, "active": True}, {"_id": 0, "user_id": 1},
    )
    users_with_install = {i["user_id"] async for i in installs_cur if i.get("user_id")}

    events_cur = db.github_funnel_events.find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1})
    users_with_event = {e["user_id"] async for e in events_cur if e.get("user_id")}

    buckets: dict[str, list[dict]] = {s: [] for s in STAGES}
    for u in candidates:
        uid = u["user_id"]
        if u.get("is_admin") or u.get("is_unlimited") or u.get("tier") == "founder":
            continue
        if u.get("first_chat_at"):  # <-- the bug: any chat == "done"
            continue
        ts = u.get("created_at")
        ts = ts.timestamp() if hasattr(ts, "timestamp") else (ts or 0)
        has_project = uid in users_with_project
        has_github = uid in users_with_install
        has_started = uid in users_with_event
        if has_project:
            stage = "stage3_no_chat"
        elif has_github:
            stage = "stage2_project_pending"
        elif has_started:
            stage = "stage1_github_started"
        else:
            stage = "stage4_fully_inactive"
        if ts and ts < cutoff:
            u["_stage"] = stage
            buckets[stage].append(u)
    return buckets


async def main() -> None:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL / DB_NAME not set in environment")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    print(f"Connected to DB: {db_name!r} (host from MONGO_URL, not printed)\n")

    old_buckets = await _classify_old(db)
    new_buckets = await classify_users(db)

    print(f"{'stage':<24} {'OLD count':>10} {'NEW count':>10}")
    print("-" * 46)
    old_total = new_total = 0
    for stage in STAGES:
        old_n, new_n = len(old_buckets[stage]), len(new_buckets[stage])
        old_total += old_n
        new_total += new_n
        print(f"{stage:<24} {old_n:>10} {new_n:>10}")
    print("-" * 46)
    print(f"{'TOTAL':<24} {old_total:>10} {new_total:>10}\n")

    old_ids = {u["user_id"] for bucket in old_buckets.values() for u in bucket}
    new_ids = {u["user_id"] for bucket in new_buckets.values() for u in bucket}
    newly_visible = new_ids - old_ids
    print(f"Users newly visible under the fix (chatted, never shipped, "
          f"previously silently dropped): {len(newly_visible)}")
    if newly_visible:
        by_uid = {u["user_id"]: u for bucket in new_buckets.values() for u in bucket}
        for uid in sorted(newly_visible):
            u = by_uid[uid]
            print(f"  - {u.get('email')!r:40} now in {u['_stage']}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
