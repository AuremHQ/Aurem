"""
routers/admin_pricing.py — Master Prompt v2 PART 3: the admin pricing
panel (founder/admin-only).

  GET  /admin/pricing/version           current + historical pricing_versions
  POST /admin/pricing/margin/preview    compute a proposed margin's impact, no write
  POST /admin/pricing/margin/confirm    apply a NEW pricing_version (future purchases only)
  GET  /admin/pricing/fixed-costs       list
  POST /admin/pricing/fixed-costs       create/update one line
  DELETE /admin/pricing/fixed-costs/{cost_id}
  GET  /admin/pricing/dashboard         realized margin/break-even/tokens-owed/...
  GET  /admin/pricing/billing-flags     refund/dispute/spike flags, open or resolved
  POST /admin/pricing/billing-flags/{flag_id}/resolve
  GET  /admin/pricing/audit-log
  GET  /admin/pricing/plan-transition-dry-run   PART 6 — read-only, writes nothing

Router-level `Depends(require_admin_dep)` gates EVERY route here at the
framework boundary (same pattern as every other admin sub-router — see
routers/admin_payments.py's own module docstring) — a route added later
inherits the gate automatically. No entry in services/admin_access_registry
means every route here defaults to OWNER-ONLY (the founder account) per
that registry's own documented "no entry => owner-only" rule — the
correct default for direct control over margin/pricing, tighter than the
generic "admin" (team-member) level other panels allow.

Every state-changing route (`margin/confirm`, `fixed-costs` POST/DELETE,
`billing-flags/.../resolve`) writes an immutable `pricing_audit_log` row
via services.pricing_engine.write_audit_row — nothing here ever mutates
or deletes a row from that collection.
"""
from __future__ import annotations

import logging
import os
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from cto_services.auth import require_admin_dep
from cto_services.db import require_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/pricing", tags=["Admin-pricing"],
    dependencies=[Depends(require_admin_dep)],
)

from routers._admin_common import _require_admin

# PRE-STEP (d) (Master Prompt v2 continuation) — rate limit + lockout
# on the state-changing endpoints below (margin/confirm, fixed-costs
# POST/DELETE, billing-flags/{id}/resolve). The GLOBAL middleware
# (main.py's `_global_rate_limit_guard`) already covers every route
# including these with a per-IP-only 300/min default — it has no
# concept of admin identity at all, so it neither slows down one
# admin spread across many IPs nor distinguishes two different admins
# sharing one office IP. These two layers add exactly that, mirroring
# routers/auth.py's existing /auth/login burst-limit + Mongo-persisted
# lockout shape:
#   Layer 1 (burst) — per-admin-id AND per-IP sliding-window buckets,
#     via the same services.rate_limiter.check_rate_limit_async
#     primitive, on all four mutation endpoints.
#   Layer 2 (lockout, margin/confirm only — the one endpoint with a
#     brute-forceable secret, the preview token's HMAC) — a Mongo-
#     persisted failed-attempt counter that locks out ALL further
#     confirm attempts from that admin id, even valid ones, for the
#     remainder of the window, same as the login lockout.
_ADMIN_MUTATE_RATE_PER_MIN = int(os.getenv("ADMIN_PRICING_MUTATE_RATE_PER_MIN", "20"))
_ADMIN_MUTATE_RATE_PER_IP_PER_MIN = int(os.getenv("ADMIN_PRICING_MUTATE_RATE_PER_IP_PER_MIN", "30"))
_ADMIN_CONFIRM_FAIL_LIMIT = int(os.getenv("ADMIN_PRICING_CONFIRM_FAIL_LIMIT", "5"))
_ADMIN_CONFIRM_LOCKOUT_MIN = int(os.getenv("ADMIN_PRICING_CONFIRM_LOCKOUT_MIN", "15"))
_RATE_LIMIT_DISABLED = os.getenv("RATE_LIMIT_DISABLED", "0") == "1"


async def _enforce_admin_mutation_rate_limit(request: Request | None, admin_id: str, *, tag: str) -> None:
    """`request` is optional only so this codebase's established
    direct-call test pattern (calling the endpoint function in-process
    instead of through FastAPI's TestClient — see test_regression_
    iter2026_11d's module docstring for why) doesn't need to fabricate
    an ASGI scope for every pre-existing call site; a REAL HTTP request
    always has FastAPI inject it regardless of this default, so the
    per-IP layer is never actually skipped in production."""
    from services.rate_limiter import check_rate_limit_async, client_ip_from_request
    if not await check_rate_limit_async(f"admin_pricing_mutate:{tag}:{admin_id}", _ADMIN_MUTATE_RATE_PER_MIN):
        raise HTTPException(
            429,
            f"Too many admin pricing requests for this account. "
            f"Limit is {_ADMIN_MUTATE_RATE_PER_MIN} per minute.",
        )
    if request is None:
        return
    ip = client_ip_from_request(request)
    if not await check_rate_limit_async(f"admin_pricing_mutate_ip:{tag}:{ip}", _ADMIN_MUTATE_RATE_PER_IP_PER_MIN):
        raise HTTPException(
            429,
            f"Too many admin pricing requests from this IP. "
            f"Limit is {_ADMIN_MUTATE_RATE_PER_IP_PER_MIN} per minute.",
        )


async def _enforce_confirm_lockout(db, admin_id: str) -> None:
    if _RATE_LIMIT_DISABLED or db is None:
        return
    row = await db.admin_pricing_confirm_attempts.find_one({"_id": admin_id})
    if not row:
        return
    cutoff = time.time()
    fresh = [ts for ts in (row.get("failed_at") or []) if cutoff - ts <= _ADMIN_CONFIRM_LOCKOUT_MIN * 60]
    if len(fresh) >= _ADMIN_CONFIRM_FAIL_LIMIT:
        oldest = min(fresh)
        retry_in = max(int(_ADMIN_CONFIRM_LOCKOUT_MIN * 60 - (cutoff - oldest)), 30)
        raise HTTPException(
            429,
            f"Too many failed margin-confirm attempts for this admin. "
            f"Try again in ~{retry_in // 60 + 1} minute(s).",
        )


async def _record_confirm_failure(db, admin_id: str) -> None:
    if db is None:
        return
    try:
        await db.admin_pricing_confirm_attempts.update_one(
            {"_id": admin_id},
            {"$push": {"failed_at": {"$each": [time.time()], "$slice": -50}}},
            upsert=True,
        )
    except Exception:
        logger.warning("admin_pricing_confirm_attempts update failed", exc_info=True)


async def _clear_confirm_failures(db, admin_id: str) -> None:
    if db is None:
        return
    try:
        await db.admin_pricing_confirm_attempts.delete_one({"_id": admin_id})
    except Exception:
        logger.warning("admin_pricing_confirm_attempts cleanup failed", exc_info=True)


@router.get("/version")
async def get_pricing_version(authorization: str | None = Header(None)) -> dict:
    await _require_admin(authorization)
    db = require_db()
    from services.pricing_engine import get_effective_pricing_version
    current = await get_effective_pricing_version(db)
    history = await db.pricing_versions.find({}).sort("effective_from", -1).to_list(100)
    return {"current": current, "history": history}


class MarginPreviewBody(BaseModel):
    margin: float | None = None
    stripe_fee_pct: float | None = None
    stripe_fee_fixed_usd: float | None = None


@router.post("/margin/preview")
async def preview_margin(
    body: MarginPreviewBody, authorization: str | None = Header(None),
) -> dict:
    """Computes what the proposed margin WOULD change, writes nothing.
    Returns a signed, short-lived, single-use token that /margin/confirm
    must echo back verbatim — binding confirm to exactly what preview
    showed and preventing replay of a captured preview+confirm pair."""
    admin_user = await _require_admin(authorization)
    db = require_db()
    from config.token_pricing import PRICE_TABLE
    from services.pricing_engine import (
        get_effective_pricing_version,
        make_preview_token,
        tokens_for_actual_cost,
        validate_margin_input,
    )

    current = await get_effective_pricing_version(db)
    if body.margin is None:
        raise HTTPException(400, "margin is required")
    try:
        margin = validate_margin_input(body.margin)
    except ValueError as e:
        raise HTTPException(400, {"error": "invalid_margin", "message": str(e)})

    stripe_fee_pct = body.stripe_fee_pct if body.stripe_fee_pct is not None else current.get("stripe_fee_pct")
    stripe_fee_fixed_usd = (
        body.stripe_fee_fixed_usd if body.stripe_fee_fixed_usd is not None
        else current.get("stripe_fee_fixed_usd")
    )
    if not isinstance(stripe_fee_pct, (int, float)) or not (0 <= stripe_fee_pct < 1):
        raise HTTPException(400, {"error": "invalid_stripe_fee_pct"})
    if not isinstance(stripe_fee_fixed_usd, (int, float)) or stripe_fee_fixed_usd < 0:
        raise HTTPException(400, {"error": "invalid_stripe_fee_fixed_usd"})

    impact = []
    for action in PRICE_TABLE:
        stats = await db.action_cost_stats.find_one({"_id": action})
        avg_cost = (stats or {}).get("avg_cost_usd_7d")
        if avg_cost:
            new_tokens = tokens_for_actual_cost(avg_cost, margin=margin)
            old_tokens = tokens_for_actual_cost(avg_cost, margin=current["margin"])
        else:
            new_tokens = old_tokens = None
        impact.append({
            "action": action, "avg_cost_usd_7d": avg_cost,
            "tokens_at_current_margin": old_tokens, "tokens_at_new_margin": new_tokens,
        })

    admin_id = admin_user.get("email") or admin_user.get("user_id") or "unknown"
    payload = {
        "margin": margin, "stripe_fee_pct": float(stripe_fee_pct),
        "stripe_fee_fixed_usd": float(stripe_fee_fixed_usd),
        # PRE-STEP (c) — binds this token to the admin who previewed
        # it. Never taken from client-submitted body fields anywhere
        # (confirm_margin below reconstructs this from ITS OWN
        # authenticated caller, not from the request body) — a
        # different admin confirming reconstructs a payload with a
        # different admin_id, which no longer matches the signature
        # computed here, and is rejected the same way a tampered
        # margin value already was.
        "admin_id": admin_id,
    }
    token_doc = make_preview_token(payload)
    return {
        "current_margin": current["margin"],
        "proposed_margin": margin,
        "impact_by_action": impact,
        "preview": token_doc,
    }


class MarginConfirmBody(BaseModel):
    margin: float
    stripe_fee_pct: float
    stripe_fee_fixed_usd: float
    expires_at: float
    token: str


@router.post("/margin/confirm")
async def confirm_margin(
    body: MarginConfirmBody, authorization: str | None = Header(None),
    request: Request = None,
) -> dict:
    user = await _require_admin(authorization)
    db = require_db()
    from services.pricing_engine import (
        get_effective_pricing_version,
        set_pricing_version,
        validate_margin_input,
        verify_and_consume_preview_token,
        write_audit_row,
    )

    confirming_admin_id = user.get("email") or user.get("user_id") or "unknown"
    await _enforce_admin_mutation_rate_limit(request, confirming_admin_id, tag="confirm")
    await _enforce_confirm_lockout(db, confirming_admin_id)

    try:
        margin = validate_margin_input(body.margin)
    except ValueError as e:
        await _record_confirm_failure(db, confirming_admin_id)
        raise HTTPException(400, {"error": "invalid_margin", "message": str(e)})

    payload = {
        "margin": margin, "stripe_fee_pct": body.stripe_fee_pct,
        "stripe_fee_fixed_usd": body.stripe_fee_fixed_usd,
        "expires_at": body.expires_at,
        # PRE-STEP (c) — reconstructed from the AUTHENTICATED caller,
        # never from a client-submitted field (MarginConfirmBody has
        # no admin_id field at all) — see preview_margin above.
        "admin_id": confirming_admin_id,
    }
    try:
        await verify_and_consume_preview_token(db, payload, body.token)
    except ValueError as e:
        await _record_confirm_failure(db, confirming_admin_id)
        raise HTTPException(409, {"error": "preview_token_invalid", "message": str(e)})

    before = await get_effective_pricing_version(db)
    actor = confirming_admin_id
    new_version = await set_pricing_version(
        db, margin=margin, stripe_fee_pct=body.stripe_fee_pct,
        stripe_fee_fixed_usd=body.stripe_fee_fixed_usd, actor=actor,
    )
    await write_audit_row(
        db, action="margin_change", actor=actor,
        before={"margin": before.get("margin"), "version_id": before.get("_id")},
        after={"margin": new_version["margin"], "version_id": new_version["_id"]},
    )
    await _clear_confirm_failures(db, confirming_admin_id)
    return {"ok": True, "new_version": new_version}


class FixedCostBody(BaseModel):
    cost_id: str | None = None
    name: str
    amount_usd_monthly: float


@router.get("/fixed-costs")
async def list_fixed_costs_endpoint(authorization: str | None = Header(None)) -> dict:
    await _require_admin(authorization)
    db = require_db()
    from services.pricing_engine import list_fixed_costs
    return {"fixed_costs": await list_fixed_costs(db)}


@router.post("/fixed-costs")
async def upsert_fixed_cost(
    body: FixedCostBody, authorization: str | None = Header(None),
    request: Request = None,
) -> dict:
    user = await _require_admin(authorization)
    db = require_db()
    from services.pricing_engine import set_fixed_cost, write_audit_row

    actor = user.get("email") or user.get("user_id") or "unknown"
    await _enforce_admin_mutation_rate_limit(request, actor, tag="fixed_cost")

    before = None
    if body.cost_id:
        before = await db.fixed_costs.find_one({"_id": body.cost_id})
    try:
        doc = await set_fixed_cost(
            db, cost_id=body.cost_id, name=body.name,
            amount_usd_monthly=body.amount_usd_monthly, actor=actor,
        )
    except ValueError as e:
        raise HTTPException(400, {"error": "invalid_fixed_cost", "message": str(e)})
    await write_audit_row(
        db, action="fixed_cost_upsert", actor=actor, before=before, after=doc,
    )
    return {"ok": True, "fixed_cost": doc}


@router.delete("/fixed-costs/{cost_id}")
async def delete_fixed_cost_endpoint(
    cost_id: str, authorization: str | None = Header(None),
    request: Request = None,
) -> dict:
    user = await _require_admin(authorization)
    db = require_db()
    from services.pricing_engine import delete_fixed_cost, write_audit_row

    actor = user.get("email") or user.get("user_id") or "unknown"
    await _enforce_admin_mutation_rate_limit(request, actor, tag="fixed_cost")

    before = await db.fixed_costs.find_one({"_id": cost_id})
    deleted = await delete_fixed_cost(db, cost_id)
    if not deleted:
        raise HTTPException(404, "fixed cost not found")
    await write_audit_row(
        db, action="fixed_cost_delete", actor=actor, before=before, after=None,
    )
    return {"ok": True}


@router.get("/dashboard")
async def get_dashboard(authorization: str | None = Header(None)) -> dict:
    await _require_admin(authorization)
    db = require_db()
    from services.pricing_engine import compute_dashboard
    return await compute_dashboard(db)


@router.get("/billing-flags")
async def get_billing_flags(
    resolved: bool | None = None, authorization: str | None = Header(None),
) -> dict:
    await _require_admin(authorization)
    db = require_db()
    from services.billing_flags import list_flags
    return {"flags": await list_flags(db, resolved=resolved)}


class ResolveFlagBody(BaseModel):
    note: str = ""


@router.post("/billing-flags/{flag_id}/resolve")
async def resolve_billing_flag(
    flag_id: str, body: ResolveFlagBody, authorization: str | None = Header(None),
    request: Request = None,
) -> dict:
    user = await _require_admin(authorization)
    db = require_db()
    from services.billing_flags import resolve_flag
    actor = user.get("email") or user.get("user_id") or "unknown"
    await _enforce_admin_mutation_rate_limit(request, actor, tag="flag_resolve")
    try:
        doc = await resolve_flag(db, flag_id, actor=actor, note=body.note)
    except ValueError as e:
        raise HTTPException(404, {"error": "flag_not_found", "message": str(e)})
    return {"ok": True, "flag": doc}


@router.get("/audit-log")
async def get_audit_log(authorization: str | None = Header(None)) -> dict:
    await _require_admin(authorization)
    db = require_db()
    from services.pricing_engine import list_audit_log, verify_audit_chain
    chain = await verify_audit_chain(db)
    return {"audit_log": await list_audit_log(db), "chain_integrity": chain}


@router.get("/plan-transition-dry-run")
async def get_plan_transition_dry_run(authorization: str | None = Header(None)) -> dict:
    """Master Prompt v2 PART 6 — read-only. Writes NOTHING: the real
    db handle is wrapped in a WriteGuardedDB before it ever reaches
    services.plan_transition.run_dry_run, which itself refuses a raw
    (unguarded) db outright. Deliberately takes NO client-supplied
    filter/query parameter at all — the report is always "every
    user," never a client-scoped subset, closing off any Mongo-
    operator-injection or scope-widening avenue before it could
    exist."""
    admin_user = await _require_admin(authorization)
    db = require_db()
    from services.plan_transition import mask_email, run_dry_run
    from services.plan_transition_write_guard import WriteGuardedDB
    from services.usage import is_founder_email
    guarded = WriteGuardedDB(db)
    report = await run_dry_run(guarded)
    # 2026-10-25 (ITEM 3(e), independent review) — per-user rows carry
    # a plain-text email. Any admin can reach this endpoint (router-
    # level `require_admin_dep`), but only the founder needs the real
    # address to act on a row; everyone else sees it masked.
    is_founder = (admin_user or {}).get("tier") == "founder" or is_founder_email((admin_user or {}).get("email"))
    if not is_founder:
        for row in report.get("rows", []):
            if "email" in row:
                row["email"] = mask_email(row["email"])
    return report


@router.get("/stale-fix-entitlement-preflight")
async def get_stale_fix_entitlement_preflight(authorization: str | None = Header(None)) -> dict:
    """ITEM 1(c) (independent review) — read-only. Lists every user
    whose `fix_entitlement` is True but has no current active-
    entitlement signal (cancelled/paused subscription that predates
    the webhook-revoke fix in routers/payments.py, or any other row
    that ends up in that shape). Writes NOTHING — real db wrapped in
    a WriteGuardedDB before it ever reaches
    services.fix_entitlement.find_stale_entitlements, which itself
    refuses a raw (unguarded) db outright. Takes no client-supplied
    filter — always "every user," closing off scope-widening or
    Mongo-operator-injection avenues. Does NOT revoke anything; see
    docs/CUTOVER_TOKEN_BILLING.md (or the Item 1 report) for the
    separate, not-yet-built, admin-confirm-token real-revoke step."""
    await _require_admin(authorization)
    db = require_db()
    from services.fix_entitlement import find_stale_entitlements
    from services.plan_transition_write_guard import WriteGuardedDB
    guarded = WriteGuardedDB(db)
    return await find_stale_entitlements(guarded)
