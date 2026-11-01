"""
services/finding_scope.py — Robot Fix List, B1/B2.

ONE helper for every read AND write of `cto_open_findings`. Resolves
the AUTHENTICATED caller's effective account (team member -> the
account owner's shared id, same semantics `routers/findings.py`
already uses for every existing endpoint) and validates the given
project_id is owned by that account — never trusts a project_id or
user_id handed in from a request body or an LLM tool-call argument.

Root cause this closes (BE2): `services/local_tools.py::save_finding`
wrote `user_id=bin_ctx.bin_id` (the CALLER's own individual user_id)
while `routers/findings.py::fix_list` reads
`user_id=ctx["effective_account_id"]` (the shared TEAM-account id).
For a solo/owner account these are the same value, so the bug never
showed; for a TEAM MEMBER they differ — their own chat-saved findings
were written under an id `fix_list` never queries, so they silently
never appeared. `save_finding` now resolves through this same helper.

B2 — real-only gate. `verified_only_filter()` is the ONE shared Mongo
filter every consumer of `cto_open_findings` (fix-list, grouped
fix-list, starter-suggestions, backlog strip, bulk-fix input) applies
so an unverified chat-audit finding never reaches the user as a real
issue. Scanner-origin rows never set `verified` explicitly (verified
by construction — the filter treats "field absent" as verified, so
legacy rows written before this change are not silently hidden).
"""
from __future__ import annotations

from fastapi import HTTPException

from services.team_permissions import check_permission


async def resolve_findings_scope(
    db, *, caller_user_id: str, project_id: str, level: str = "read",
) -> dict:
    """Returns {"user_id": <effective_account_id>, "project_id": project_id}.

    `caller_user_id` MUST be the authenticated session's own user_id
    (JWT-derived) — never a value read from a request body or an LLM
    tool-call argument. Raises HTTPException(400/403/404) on an
    invalid or foreign project, the same failure mode every existing
    caller of this data already expects.
    """
    if not (isinstance(caller_user_id, str) and caller_user_id):
        raise HTTPException(400, "missing_user")
    if not (isinstance(project_id, str) and project_id):
        raise HTTPException(400, "missing_project_id")
    ctx = await check_permission(
        {"user_id": caller_user_id}, "projects", level, db=db,
    )
    user_id = ctx["effective_account_id"]
    proj = await db.cto_projects.find_one(
        {"project_id": project_id}, {"_id": 0, "user_id": 1},
    )
    if not proj:
        raise HTTPException(404, "project_not_found")
    # Part 2b R5 — fail CLOSED. A project doc with no verifiable
    # owner (missing/empty user_id — shouldn't happen, but a corrupt
    # or legacy row must never be treated as "unowned, anyone's") is
    # denied to EVERYONE, not silently granted to whoever asked.
    if proj.get("user_id") != user_id:
        raise HTTPException(403, "not_your_project")
    return {"user_id": user_id, "project_id": project_id}


def verified_only_filter() -> dict:
    """Mongo filter fragment excluding ONLY explicitly-unverified rows.
    `{"verified": False}` is the single state this hides — absent
    (legacy / scanner-origin rows that never set the field) and
    `True` both count as real. Merge into any `cto_open_findings`
    query with `{**base_filter, **verified_only_filter()}`."""
    return {"verified": {"$ne": False}}


async def drop_unverified_findings(
    db, *, user_id: str, project_id: str, findings: list[dict],
) -> list[dict]:
    """B2 — "bulk-fix input" must also exclude unverified findings,
    even when the caller POSTs a findings array directly rather than
    going through the (already-filtered) fix-list endpoint. A finding
    with NO matching `cto_open_findings` row (e.g. a fresh scanner
    result never persisted) is passed through unchanged — this can
    only ever DROP a finding explicitly marked `verified=False`,
    never block one we have no tracking data for."""
    if not findings:
        return findings
    ids = [f.get("id") or f.get("finding_id") for f in findings if (f.get("id") or f.get("finding_id"))]
    if not ids:
        return findings
    try:
        rows = await db.cto_open_findings.find(
            {"user_id": user_id, "project_id": project_id,
             "finding_id": {"$in": ids}, "verified": False},
            {"_id": 0, "finding_id": 1},
        ).to_list(len(ids))
    except Exception as e:                                   # noqa: BLE001
        # Fail OPEN, not closed — same contract as "no matching row":
        # if we can't positively identify an unverified finding, we
        # never block it. A real Mongo failure here is surfaced by
        # the dozens of OTHER db calls in this same request path, so
        # staying silent here doesn't hide anything load-bearing.
        import logging
        logging.getLogger("aurem.finding_scope").warning(
            "drop_unverified_findings query failed (passing through unfiltered): %r", e,
        )
        return findings
    blocked = {r["finding_id"] for r in rows}
    if not blocked:
        return findings
    return [
        f for f in findings
        if (f.get("id") or f.get("finding_id")) not in blocked
    ]


_FINDING_SHAPE_PROJECTION = {
    "_id": 0, "finding_id": 1, "rule_id": 1, "file": 1, "line": 1,
    "severity": 1, "title": 1, "message": 1, "fix_hint": 1, "category": 1,
}


def _row_to_finding(row: dict) -> dict:
    return {
        "id": row.get("finding_id"), "finding_id": row.get("finding_id"),
        "rule_id": row.get("rule_id"), "file": row.get("file"),
        "line": row.get("line"), "severity": row.get("severity"),
        "title": row.get("title"), "message": row.get("message"),
        "fix_hint": row.get("fix_hint"), "category": row.get("category"),
    }


async def resolve_findings_by_ids(
    db, *, user_id: str, project_id: str, finding_ids: list[str],
) -> list[dict]:
    """Part 2b F7 — authoritative server-side resolution. Loads the
    REAL rows from cto_open_findings (effective account + project +
    verified + status=open), never trusting a client-supplied
    severity/title/fix_hint. Silently drops any id that doesn't match
    a real, verified, open, owned row — never raises on a bad id."""
    if not finding_ids:
        return []
    rows = await db.cto_open_findings.find(
        {"user_id": user_id, "project_id": project_id, "status": "open",
         "finding_id": {"$in": finding_ids}, **verified_only_filter()},
        _FINDING_SHAPE_PROJECTION,
    ).to_list(len(finding_ids))
    return [_row_to_finding(r) for r in rows]


async def reconcile_findings_against_stored_rows(
    db, *, user_id: str, project_id: str, findings: list[dict],
) -> list[dict]:
    """Part 2b F7 — for the LEGACY `findings` array input path: never
    trust severity/title/message/fix_hint from the client for billing
    or LLM prompts. Any item that matches a stored, verified, open
    row by id gets those fields OVERWRITTEN from the stored row. An
    item with no matching row is passed through UNCHANGED — R1's
    presence pre-check (the static scanner re-detecting it on the
    current file content, before any LLM call) is what decides
    whether an untracked item is real, not this function."""
    if not findings:
        return findings
    ids = [f.get("id") or f.get("finding_id") for f in findings if (f.get("id") or f.get("finding_id"))]
    if not ids:
        return findings
    try:
        rows = await db.cto_open_findings.find(
            {"user_id": user_id, "project_id": project_id, "status": "open",
             "finding_id": {"$in": ids}, **verified_only_filter()},
            _FINDING_SHAPE_PROJECTION,
        ).to_list(len(ids))
    except Exception as e:                                   # noqa: BLE001
        # Fail OPEN — same contract as drop_unverified_findings: this
        # is a reconciliation step, not the security gate itself
        # (drop_unverified_findings, called right after this, is).
        import logging
        logging.getLogger("aurem.finding_scope").warning(
            "reconcile_findings_against_stored_rows query failed (passing through unreconciled): %r", e,
        )
        return findings
    by_id = {r["finding_id"]: r for r in rows if r.get("finding_id")}
    reconciled = []
    for f in findings:
        fid = f.get("id") or f.get("finding_id")
        stored = by_id.get(fid)
        if stored:
            reconciled.append({**f, **_row_to_finding(stored)})
        else:
            reconciled.append(f)
    return reconciled
