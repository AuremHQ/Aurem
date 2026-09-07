"""
scripts/audit_stuck_github_installs.py — READ-ONLY audit.

Finds users who installed the GitHub App but never connected a
project ("stuck" installs). Makes ZERO writes to any collection —
`db.<collection>.find`/`.count_documents` only, nothing else.

Definition of "stuck" (confirm/adjust with founder before any action):
  - github_installations: active == True for this user
  - AND cto_projects: zero documents for this user
  - AND the install is older than --days (default 14 — early-stage
    app, most real "connect a repo" onboarding completes same-day;
    14 days is long enough to rule out someone still mid-onboarding
    while still catching genuinely abandoned installs)
  - Excludes admin / is_unlimited / tier=="founder" accounts (same
    exclusion the funnel_nudge_cron uses for internal/test accounts —
    see services/funnel_nudge_cron.py::classify_users)

`last_active` is NOT a stored field anywhere in this codebase (no
dev_users.last_active / last_login_at exists) — this script derives
it as the most recent of {dev_users.created_at, MAX(chat_sessions.
updated_at) for that user}, clearly labeled `last_active_derived` in
the output so it's never mistaken for a real stored column.

Usage:
    python scripts/audit_stuck_github_installs.py [--days 14]

Reads MONGO_URL/DB_NAME from the environment exactly like the app
does — point it at whichever DB you want to audit. Prints a table +
final count. No CLI flag exists to make this script write anything.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

from motor.motor_asyncio import AsyncIOMotorClient


async def main(days: int) -> None:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL / DB_NAME not set in environment")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    cutoff_ts = time.time() - (days * 86400)

    print(f"Connected to DB: {db_name!r} (host from MONGO_URL, not printed)")
    print(f"Cutoff: installs older than {days} days ago\n")

    # 1) Earliest ACTIVE install per user.
    installs_by_user: dict[str, dict] = {}
    cur = db.github_installations.find(
        {"active": True},
        {"_id": 0, "user_id": 1, "installation_id": 1, "created_at": 1},
    )
    async for row in cur:
        uid = row.get("user_id")
        if not uid:
            continue
        ts = row.get("created_at")
        ts = ts.timestamp() if hasattr(ts, "timestamp") else (ts or 0)
        if uid not in installs_by_user or ts < installs_by_user[uid]["ts"]:
            installs_by_user[uid] = {
                "ts": ts, "installation_id": row.get("installation_id"),
            }

    old_installs = {
        uid: v for uid, v in installs_by_user.items() if v["ts"] and v["ts"] < cutoff_ts
    }
    if not old_installs:
        print("No installs older than the cutoff. Nothing to report.")
        return

    ids = list(old_installs.keys())

    # 2) Which of those users have ANY project? (exclude them)
    has_project: set[str] = set()
    cur = db.cto_projects.find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1})
    async for p in cur:
        if p.get("user_id"):
            has_project.add(p["user_id"])

    stuck_ids = [uid for uid in ids if uid not in has_project]
    if not stuck_ids:
        print("All old installs already have a project. Nothing to report.")
        return

    # 3) dev_users lookup — exclude admin/unlimited/founder.
    users_by_id: dict[str, dict] = {}
    cur = db.dev_users.find(
        {"user_id": {"$in": stuck_ids}},
        {"_id": 0, "user_id": 1, "email": 1, "created_at": 1,
         "is_admin": 1, "is_unlimited": 1, "tier": 1},
    )
    async for u in cur:
        if u.get("is_admin") or u.get("is_unlimited") or u.get("tier") == "founder":
            continue
        users_by_id[u["user_id"]] = u

    final_ids = [uid for uid in stuck_ids if uid in users_by_id]
    if not final_ids:
        print("All stuck installs belong to admin/unlimited/founder accounts "
              "(excluded). Nothing to report.")
        return

    # 4) Best-effort last_active_derived from chat_sessions.updated_at.
    last_chat: dict[str, float] = {}
    cur = db.chat_sessions.find(
        {"user_id": {"$in": final_ids}}, {"_id": 0, "user_id": 1, "updated_at": 1},
    )
    async for s in cur:
        uid = s.get("user_id")
        ts = s.get("updated_at") or 0
        if uid and ts > last_chat.get(uid, 0):
            last_chat[uid] = ts

    rows = []
    for uid in final_ids:
        u = users_by_id[uid]
        created = u.get("created_at")
        created_ts = created.timestamp() if hasattr(created, "timestamp") else (created or 0)
        last_active = max(last_chat.get(uid, 0), created_ts)
        rows.append({
            "user_id":         uid,
            "email":           u.get("email"),
            "installation_id": old_installs[uid]["installation_id"],
            "installed_at":    time.strftime("%Y-%m-%d", time.gmtime(old_installs[uid]["ts"])),
            "last_active_derived": (
                time.strftime("%Y-%m-%d", time.gmtime(last_active)) if last_active else "unknown"
            ),
        })

    rows.sort(key=lambda r: r["installed_at"])
    print(f"{'user_id':<26} {'email':<32} {'installation_id':<16} "
          f"{'installed_at':<12} {'last_active_derived':<12}")
    print("-" * 102)
    for r in rows:
        print(f"{r['user_id']:<26} {str(r['email']):<32} {str(r['installation_id']):<16} "
              f"{r['installed_at']:<12} {r['last_active_derived']:<12}")

    print(f"\nTOTAL stuck installs (read-only, nothing changed): {len(rows)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    asyncio.run(main(args.days))
