"""
services/query_indexes.py — read-path indexes for hot lookups.

`scripts/init_prod_collections.py` covers the per-user listing shapes and
`services/db_indexes.py` the unique/dedup constraints. This module holds the
remaining lookups that filter on a field no declared index leads with, so
they fall back to a collection scan. Only plain non-unique indexes live
here: they can never reject a write, and a failed build is harmless.

Runs from the boot-maintenance background task (see
`db_indexes.ensure_dedup_indexes`), never inline in lifespan — `create_index`
blocks until the build finishes, which on a large collection would delay boot.
"""
from __future__ import annotations

import logging
from typing import List

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure

logger = logging.getLogger("aurem.query_indexes")

# (collection, keys, name, sparse). Sparse where the field is only present on
# a subset of documents, so the index doesn't carry every row.
QUERY_INDEX_SPEC: list[tuple[str, list, str, bool]] = [
    ("chat_sessions", [("session_id", ASCENDING)], "ix_chat_sessions_session_id", False),
    ("dev_users", [("stripe_sub_id", ASCENDING)], "ix_dev_users_stripe_sub_id", True),
    ("cto_projects", [("installation_id", ASCENDING)], "ix_cto_projects_installation_id", True),
    ("cto_projects", [("github_owner", ASCENDING), ("github_repo", ASCENDING)],
     "ix_cto_projects_owner_repo", False),
    ("cto_payments", [("session_id", ASCENDING)], "ix_cto_payments_session_id", True),
    ("chat_request_ledger", [("request_id", ASCENDING)], "ix_chat_request_ledger_request_id", False),
    ("admin_team_members", [("user_id", ASCENDING), ("status", ASCENDING)],
     "ix_admin_team_members_user_status", False),
    ("loop_sessions", [("state", ASCENDING), ("created_at", DESCENDING)],
     "ix_loop_sessions_state_created", False),
    ("real_llm_usage", [("user_id", ASCENDING), ("ts", DESCENDING)], "ix_real_llm_usage_user_ts", False),
    ("real_llm_usage", [("task_id", ASCENDING)], "ix_real_llm_usage_task_id", True),
    ("github_funnel_events", [("user_id", ASCENDING), ("ts", DESCENDING)],
     "ix_github_funnel_events_user_ts", False),
    ("github_funnel_events", [("stage", ASCENDING), ("ts", DESCENDING)],
     "ix_github_funnel_events_stage_ts", False),
    ("first_scan_results", [("project_id", ASCENDING)], "ix_first_scan_results_project_id", False),
    ("aurem_cto_deploy_runs", [("run_id", ASCENDING)], "ix_aurem_cto_deploy_runs_run_id", False),
    ("visual_bug_reports", [("report_id", ASCENDING)], "ix_visual_bug_reports_report_id", False),
    ("warm_start_jobs", [("job_id", ASCENDING)], "ix_warm_start_jobs_job_id", False),
    ("scaffold_drafts", [("draft_id", ASCENDING), ("user_id", ASCENDING)],
     "ix_scaffold_drafts_draft_user", False),
    ("team_members", [("member_id", ASCENDING)], "ix_team_members_member_id", False),
    ("admin_team_invites", [("invite_id", ASCENDING)], "ix_admin_team_invites_invite_id", False),
    ("admin_team_invites", [("token_hash", ASCENDING)], "ix_admin_team_invites_token_hash", True),
    ("user_seo_claims", [("claim_id", ASCENDING)], "ix_user_seo_claims_claim_id", False),
    ("loop_outcomes", [("ship_branch", ASCENDING)], "ix_loop_outcomes_ship_branch", True),
]


async def ensure_query_indexes(db) -> List[dict]:
    """Create every index in QUERY_INDEX_SPEC; one failure never stops the rest."""
    if db is None:
        return []
    results: List[dict] = []
    for coll, keys, name, sparse in QUERY_INDEX_SPEC:
        try:
            await db[coll].create_index(keys, name=name, sparse=sparse, background=True)
            results.append({"collection": coll, "name": name, "ok": True})
        except OperationFailure as e:
            # 85/86/68: same keys already indexed under another name/options.
            results.append({"collection": coll, "name": name, "ok": True, "existed": True,
                            "note": str(e.details or e)[:120]})
        except Exception as e:  # noqa: BLE001
            results.append({"collection": coll, "name": name, "ok": False, "error": str(e)[:200]})
            logger.warning("query index %s on %s failed: %r", name, coll, e)
    return results


async def get_query_index_report(db) -> dict:
    """Which QUERY_INDEX_SPEC indexes exist right now (by name)."""
    if db is None:
        return {"available": False}
    missing: list[str] = []
    try:
        existing = set(await db.list_collection_names())
        by_coll: dict[str, set] = {}
        for coll, _keys, name, _sparse in QUERY_INDEX_SPEC:
            if coll not in existing:
                continue  # collection not created yet: nothing to scan, nothing missing
            if coll not in by_coll:
                rows = await db[coll].list_indexes().to_list(length=100)
                by_coll[coll] = {r.get("name") for r in rows}
            if name not in by_coll[coll]:
                missing.append(f"{coll}.{name}")
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:200]}
    return {"available": True, "expected": len(QUERY_INDEX_SPEC), "missing": missing}
