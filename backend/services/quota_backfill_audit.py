"""
services/quota_backfill_audit.py — Q3 (2026-02)

DRY-RUN ONLY. Lists EXISTING free-tier users who are still exhausted
even under the NEW (post-Q1) $0.25 lifetime USD budget, have at least
one connected project (so the exhaustion actually blocked real work,
not an account that was never used), and are NOT dormant (made at
least one authenticated request after signup).

This module performs ZERO writes. It never calls grant_tokens, never
updates dev_users, never inserts a grant record. An admin reviews the
returned list and, if they choose to act, uses the EXISTING
POST /admin/users/{id}/grant-tokens endpoint manually per-user.
"""
from __future__ import annotations

from typing import Any

from config.quota_limits import EXISTING_USER_TOPUP_USD
from services.name_integrity_audit import ran_against_db
from services.usage import get_usage

# Safety cap — this is an admin report, not a hot-path query. Bounds
# how many candidate free users we ever pull per call.
MAX_CANDIDATES = 500


def _severity_bucket(used_usd: float, effective_limit_usd: float) -> str:
    if effective_limit_usd <= 0:
        return "unknown"
    ratio = used_usd / effective_limit_usd
    if ratio < 2:
        return "slightly_over_under_2x"
    if ratio < 5:
        return "moderately_over_2x_to_5x"
    return "heavily_over_5x_plus"


async def run_dry_run_audit(db) -> dict[str, Any]:
    """Read-only. Returns counts, a severity-grouped breakdown, the
    candidate list, and the proposed (config-driven) top-up amount.
    Applies NOTHING."""
    candidates = await db.dev_users.find(
        {
            "$and": [
                {"$or": [{"tier": "free"}, {"tier": {"$exists": False}}]},
                {"last_seen_at": {"$exists": True, "$ne": None}},
            ]
        },
        {"user_id": 1, "email": 1, "name": 1, "created_at": 1, "last_seen_at": 1},
    ).limit(MAX_CANDIDATES).to_list(MAX_CANDIDATES)

    matches: list[dict[str, Any]] = []
    scanned_dormant_excluded = 0
    for u in candidates:
        user_id = u.get("user_id")
        if not user_id:
            continue
        try:
            usage = await get_usage(user_id)
        except Exception:                                   # noqa: BLE001
            continue
        if not usage.get("is_exhausted_usd"):
            continue
        project_count = await db.cto_projects.count_documents({"user_id": user_id})
        if project_count < 1:
            continue
        matches.append({
            "user_id": user_id,
            "email": u.get("email"),
            "name": u.get("name"),
            "used_usd": usage.get("used_usd"),
            "effective_limit_usd": usage.get("effective_limit_usd"),
            "project_count": project_count,
            "last_seen_at": u.get("last_seen_at"),
            "created_at": u.get("created_at"),
            "severity": _severity_bucket(
                usage.get("used_usd") or 0.0, usage.get("effective_limit_usd") or 0.0
            ),
            "proposed_topup_usd": EXISTING_USER_TOPUP_USD,
        })

    grouped: dict[str, int] = {}
    for m in matches:
        grouped[m["severity"]] = grouped.get(m["severity"], 0) + 1

    return {
        "dry_run": True,
        "applied_any_grant": False,
        "ran_against": ran_against_db(),
        "scanned_candidates": len(candidates),
        "matched_count": len(matches),
        "grouped_by_severity": grouped,
        "proposed_topup_usd_each": EXISTING_USER_TOPUP_USD,
        "users": matches,
    }
