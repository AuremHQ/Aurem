"""
routers/self_bugs_admin.py — Item 3 (2026-08-31)

Admin dashboard for `services/self_bug.py`'s structured self-bug
ledger (`ora_self_bugs`) + the learned-recurrence counter
(`self_bug_learned`). Founder-gated via `require_admin`, same pattern
as routers/suggestions.py's admin endpoints.

2026-09-21 — Self-Repair audit (Part 3/4): this used to be strictly
read-only ("no mutate/delete route"). It now has exactly two narrow
mutate actions — mark-fixed/re-open, and approve/reject a Part-4
PROPOSE-ONLY fix suggestion — neither of which touches the ledger
rows themselves (`ora_self_bugs` stays append-only/immutable); both
only update the learned-pattern row's status/review fields.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from cto_services.auth import require_admin, require_admin_dep
from cto_services.db import require_db
from services.self_bug import SELF_BUG_TYPES, signature, mark_fixed

router = APIRouter(prefix="/admin/self-bugs", tags=["Admin — Self-Repair"],
                   dependencies=[Depends(require_admin_dep)])


def _iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


@router.get("/list")
async def list_self_bugs(
    type: Optional[str] = None,
    limit: int = 200,
    authorization: Optional[str] = Header(None),
    request: Request = None,
):
    """Listing, sorted by recurrence (times_seen) desc so the "this
    keeps happening" bugs bubble to the top — the whole point of P7's
    learned-recurrence counter. `type` optionally filters to one of
    SELF_BUG_TYPES.

    2026-09-21 — Part 2.1 fix: `signature()` now needs `evidence` too
    (see services/self_bug.py) so distinct occurrences of the same
    bug type get distinct recurrence counts instead of all sharing
    one identical times_seen/last_seen. Part 3 adds first_seen/
    status/fixed_on/fixed_by. Part 4 surfaces any pending
    propose-only fix suggestion."""
    await require_admin(authorization, request=request)
    db = require_db()
    q: dict = {}
    if type in SELF_BUG_TYPES:
        q["type"] = type
    limit = max(1, min(limit, 500))

    learned_by_sig: dict[str, dict] = {}
    async for doc in db.self_bug_learned.find({}, {"_id": 0}):
        learned_by_sig[doc.get("signature", "")] = doc

    rows = []
    async for r in db.ora_self_bugs.find(q, {"_id": 0}).sort("ts", -1).limit(limit):
        sig = r.get("signature") or signature(
            r.get("type", ""), r.get("context") or {}, r.get("evidence") or "",
        )
        learned = learned_by_sig.get(sig, {})
        ts = r.get("ts")
        rows.append({
            "type":             r.get("type"),
            "source":           r.get("source"),
            "what_user_saw":    r.get("what_user_saw"),
            "likely_cause":     r.get("likely_cause"),
            "confidence":       r.get("confidence"),
            "severity":         r.get("severity"),
            "proposed_fix":     r.get("proposed_fix"),
            "signature":        sig,
            "times_seen":       learned.get("times_seen", 1),
            "first_seen":       _iso(learned.get("first_seen")),
            "last_seen":        _iso(learned.get("last_seen")),
            "status":           learned.get("status", "open"),
            "fixed_on":         _iso(learned.get("fixed_on")),
            "fixed_by":         learned.get("fixed_by"),
            "proposed_fix_text": learned.get("proposed_fix_text"),
            "proposed_fix_review_status": learned.get("proposed_fix_review_status"),
            "ts":               _iso(ts),
        })
    # Recurring bugs bubble up first (times_seen desc); most-recent
    # breaks ties (stable sort: sort by ts desc first, then by
    # times_seen desc — ties keep their ts-desc relative order).
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    rows.sort(key=lambda r: r["times_seen"], reverse=True)
    return {"self_bugs": rows, "count": len(rows), "types": sorted(SELF_BUG_TYPES)}


class _MarkFixedBody(BaseModel):
    signature: str
    status: str = "fixed"   # "fixed" | "open" (re-open)


@router.post("/mark-fixed")
async def mark_self_bug_fixed(
    body: _MarkFixedBody,
    authorization: Optional[str] = Header(None),
    request: Request = None,
):
    """Part 3 — manual fix-status action. See services/self_bug.py's
    mark_fixed() docstring for why this is manual-only (no automatic
    deploy/commit linkage exists yet)."""
    admin = await require_admin(authorization, request=request)
    if body.status not in ("fixed", "open"):
        raise HTTPException(400, "status must be fixed|open")
    ok = await mark_fixed(
        body.signature, body.status,
        fixed_by=(admin.get("email") or admin.get("user_id")),
    )
    if not ok:
        raise HTTPException(404, "no self-bug learned-pattern row for this signature")
    return {"ok": True, "signature": body.signature, "status": body.status}


class _ReviewProposalBody(BaseModel):
    signature: str
    decision: str   # "approved" | "rejected"


@router.post("/review-proposal")
async def review_self_bug_proposal(
    body: _ReviewProposalBody,
    authorization: Optional[str] = Header(None),
    request: Request = None,
):
    """Part 4 — human review of a PROPOSE-ONLY fix suggestion (see
    services/self_bug.py::propose_fix). This ONLY records the human's
    decision on the learned-pattern row. It does NOT apply the fix,
    and does NOT flip any auto-apply switch — no such switch exists.
    Per spec, auto-apply for a bug CATEGORY would only ever be
    considered after several of these approvals accumulate for that
    exact category, as a separate future opt-in — not built here."""
    admin = await require_admin(authorization, request=request)
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved|rejected")
    db = require_db()
    r = await db.self_bug_learned.update_one(
        {"signature": body.signature},
        {"$set": {
            "proposed_fix_review_status": body.decision,
            "proposed_fix_reviewed_at": datetime.now(timezone.utc).timestamp(),
            "proposed_fix_reviewed_by": admin.get("email") or admin.get("user_id"),
        }},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "no self-bug learned-pattern row for this signature")
    return {"ok": True, "signature": body.signature, "decision": body.decision}
