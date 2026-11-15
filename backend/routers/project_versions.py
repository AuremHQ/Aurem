"""
routers/project_versions.py — Part 2c, Project Versions feature (B3).

GET /projects/{project_id}/versions — the private "Changes" page's
data source. Gated by feature flag `project_versions` (default OFF —
this task never turns it on; flag off -> 404, the same "pretend the
route doesn't exist" contract fix_pipeline and other gated routers
use). Ownership via services.finding_scope.resolve_findings_scope —
same fail-closed, team-aware project-ownership check every other
project-scoped read in this codebase already uses.

Response is an explicit field WHITELIST — no user_id, no token cost,
no raw internal errors. Free tier sees only the latest 10 (+ `total`
and `locked_count` so the UI can render "Showing your latest 10 of
N."); every paid tier sees everything up to the `limit` query param
(clamped 1..50).
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.feature_flags import is_enabled
from services.team_permissions import check_permission

router = APIRouter(prefix="/projects", tags=["Project Versions"])

# Explicit whitelist — never user_id, never anything token/cost-
# related, never a raw internal error string.
_WHITELIST_FIELDS = (
    "version_id", "n", "merged_sha", "pr_url", "pr_number",
    "branch", "source", "summary", "merged_at", "status",
)
_FREE_TIER_CAP = 10
_MAX_LIMIT = 50


# Same _gate pattern as routers/fix_pipeline.py — team-resolved
# effective account, never the caller's own raw user_id.
async def _gate(authorization: str | None, level: str, db=None) -> tuple[dict, dict]:
    caller = await current_dev(authorization)
    ctx = await check_permission(caller, "projects", level, db=db)
    effective_user = {**caller, "user_id": ctx["effective_account_id"]}
    return caller, effective_user


def _to_whitelisted_response(row: dict) -> dict:
    return {k: row.get(k) for k in _WHITELIST_FIELDS}


@router.get("/{project_id}/versions")
async def list_project_versions(
    project_id: str,
    limit: int = 50,
    authorization: str | None = Header(None),
) -> dict:
    caller, effective = await _gate(authorization, "read")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    if not await is_enabled(
        "project_versions", user_id=effective["user_id"], tier=caller.get("tier"),
    ):
        # Flag off -> pretend the route doesn't exist, same contract
        # as every other flag-gated endpoint in this codebase.
        raise HTTPException(404, "Not found")

    from services.finding_scope import resolve_findings_scope
    scope = await resolve_findings_scope(
        db, caller_user_id=caller["user_id"], project_id=project_id, level="read",
    )
    owner_user_id = scope["user_id"]

    limit = max(1, min(int(limit), _MAX_LIMIT))

    from services.subscription_tiers import Tier, _coerce
    tier = _coerce(caller.get("tier"))

    base_filter = {"user_id": owner_user_id, "project_id": project_id}
    total = await db.project_versions.count_documents(base_filter)

    effective_limit = _FREE_TIER_CAP if tier == Tier.FREE else limit
    rows = await db.project_versions.find(base_filter) \
        .sort("n", -1).limit(effective_limit).to_list(effective_limit)

    locked_count = max(0, total - len(rows)) if tier == Tier.FREE else 0
    return {
        "ok": True,
        "versions": [_to_whitelisted_response(r) for r in rows],
        "total": total,
        "locked_count": locked_count,
    }
