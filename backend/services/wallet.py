"""
services/wallet.py — Master Prompt Step B: token wallet (grant / hold /
settle / release / balance), gated by TOKEN_BILLING_MODE.

Buckets (stored on `wallets.buckets`, one doc per user, `_id = user_id`):
  - promo:     promotional tokens. May carry a per-grant `expires_at`.
  - plan:      monthly subscription allotment. Expires at the NEXT
               monthly refill (run_monthly_grant_job replaces it, never
               accumulates — see B6).
  - purchased: top-up pack tokens. NEVER expire.

Spend order: promo -> plan -> purchased (cheapest-to-the-business
first — a never-expiring purchased token is the most valuable to keep
on the books longest).

Every balance mutation (grant/hold/settle/release) writes EXACTLY ONE
row to `wallet_ledger`, keyed by a caller-supplied `idempotency_key` as
the document's own `_id`. Retrying the same key is a guaranteed no-op:
the ledger insert either succeeds once, or raises a Mongo duplicate-key
error that this module catches and compensates (reverses whatever
bucket mutation it had just sped-acted on) before returning the
ORIGINAL result. No separate unique index is needed — `_id` already
enforces it.

Balance invariant — a bucket can never go negative. `hold()` deducts
across all three buckets in ONE atomic Mongo aggregation-pipeline
update (`_atomic_bucket_spend`), which computes "is total >= amount"
and the per-bucket deltas server-side, in a single document operation.
MongoDB guarantees single-document operations are atomic regardless of
how many callers race for the same document — two concurrent holds
against a balance that can only satisfy one of them will have exactly
one succeed and one see `_ok: False` (no deduction at all for the
loser), verified in
tests/test_regression_iter2026_10h_wallet_hold_settle_release.py with
real concurrent asyncio.gather() calls against a real Mongo instance
(a dict-based mock cannot meaningfully prove this guarantee).

A run already holding tokens is allowed to FINISH even if the wallet
balance drops to exactly zero mid-run — `settle()`/`release()` never
re-check balance, they only ever finalize/refund a hold that was
already reserved at hold()-time. This happens by construction, not by
a special case.

TOKEN_BILLING_MODE (env, default "off"):
  off    — assert_wallet() is a pure no-op. No tokens are ever moved.
           Equivalent to today's un-metered behavior.
  shadow — assert_wallet() computes what WOULD be charged/blocked and
           logs it, but NEVER calls hold() (no real tokens move) and
           NEVER raises. Safe to run in prod to collect real signal.
  on     — assert_wallet() raises a typed 402 if the balance can't
           cover the action's price, else calls hold() for real.

assert_wallet() is the ONLY mode-aware entry point callers should use.
grant()/hold()/settle()/release() themselves are mode-INDEPENDENT —
they always do exactly what they say, which is what lets "shadow" mode
compute a real would-be balance check without ever calling them for
the hold.

NEVER gated by assert_wallet, regardless of mode (callers simply never
invoke it for these): viewing results/issues, viewing a PR, rollback,
export, login, billing pages.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from config.token_pricing import tokens_for

logger = logging.getLogger("aurem.wallet")

BUCKETS = ("promo", "plan", "purchased")
SPEND_ORDER = ("promo", "plan", "purchased")
_VALID_MODES = ("off", "shadow", "on")


class InsufficientTokens(Exception):
    def __init__(self, *, user_id: str, needed: int, balance: int):
        self.user_id = user_id
        self.needed = needed
        self.balance = balance
        super().__init__(
            f"insufficient tokens: user={user_id} needed={needed} balance={balance}"
        )


def billing_mode() -> str:
    """TOKEN_BILLING_MODE env, default "off". An unrecognised value
    fails SAFE to "off" (logged once) rather than risking an accidental
    typo silently turning on real enforcement."""
    raw = (os.environ.get("TOKEN_BILLING_MODE") or "off").strip().lower()
    if raw not in _VALID_MODES:
        logger.warning(
            "[wallet] TOKEN_BILLING_MODE=%r is not one of %s — "
            "defaulting to 'off'", raw, _VALID_MODES,
        )
        return "off"
    return raw


def _now() -> datetime:
    return datetime.now(UTC)


def _year_month(dt: datetime | None = None) -> str:
    dt = dt or _now()
    return f"{dt.year:04d}-{dt.month:02d}"


async def get_balance(db, user_id: str) -> dict:
    """Current balance. Never raises — a user with no wallet doc yet
    simply has zero everywhere."""
    w = await db.wallets.find_one({"_id": user_id}) or {}
    buckets = w.get("buckets") or {}
    promo = int(buckets.get("promo") or 0)
    plan = int(buckets.get("plan") or 0)
    purchased = int(buckets.get("purchased") or 0)
    return {
        "user_id": user_id,
        "promo": promo, "plan": plan, "purchased": purchased,
        "spendable": promo + plan + purchased,
        "holds_count": len(w.get("holds") or {}),
        "plan_refill_at": w.get("plan_refill_at"),
        "month_baseline": (w.get("month_baseline") or {}).get("amount"),
    }


async def _atomic_bucket_spend(db, user_id: str, amount: int) -> dict | None:
    """Atomically deduct `amount` tokens across buckets in SPEND_ORDER.
    Returns the post-update wallet doc (with `_ok`/`_take_*` debug
    fields) on success, or a doc with `_ok: False` and ZERO deduction
    if the total available was less than `amount`. Returns None only
    if the wallet doc doesn't exist at all (caller should treat that
    as balance=0, i.e. always insufficient)."""
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount}")
    pipeline = [
        {"$set": {"buckets": {"$ifNull": ["$buckets", {"promo": 0, "plan": 0, "purchased": 0}]}}},
        {"$set": {"_total": {"$add": [
            {"$ifNull": ["$buckets.promo", 0]},
            {"$ifNull": ["$buckets.plan", 0]},
            {"$ifNull": ["$buckets.purchased", 0]},
        ]}}},
        {"$set": {"_ok": {"$gte": ["$_total", amount]}}},
        {"$set": {"_take_promo": {"$cond": [
            "$_ok", {"$min": [{"$ifNull": ["$buckets.promo", 0]}, amount]}, 0,
        ]}}},
        {"$set": {"_take_plan": {"$cond": [
            "$_ok",
            {"$min": [{"$ifNull": ["$buckets.plan", 0]},
                      {"$subtract": [amount, "$_take_promo"]}]},
            0,
        ]}}},
        {"$set": {"_take_purchased": {"$cond": [
            "$_ok",
            {"$subtract": [amount, {"$add": ["$_take_promo", "$_take_plan"]}]},
            0,
        ]}}},
        {"$set": {
            "buckets.promo": {"$subtract": [{"$ifNull": ["$buckets.promo", 0]}, "$_take_promo"]},
            "buckets.plan": {"$subtract": [{"$ifNull": ["$buckets.plan", 0]}, "$_take_plan"]},
            "buckets.purchased": {"$subtract": [{"$ifNull": ["$buckets.purchased", 0]}, "$_take_purchased"]},
            "updated_at": _now(),
        }},
    ]
    doc = await db.wallets.find_one_and_update(
        {"_id": user_id}, pipeline,
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    return doc


async def grant(
    db, *, user_id: str, bucket: str, amount: int,
    idempotency_key: str, source: str, expires_at: datetime | None = None,
) -> dict:
    """Add `amount` tokens to `bucket`. Idempotent on `idempotency_key`."""
    if bucket not in BUCKETS:
        raise ValueError(f"unknown bucket {bucket!r} — expected one of {BUCKETS}")
    if amount <= 0:
        raise ValueError(f"grant amount must be positive, got {amount}")
    existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if existing:
        return {"ok": True, "idempotent_replay": True, "ledger": existing}

    now = _now()
    await db.wallets.update_one(
        {"_id": user_id},
        {
            "$inc": {f"buckets.{bucket}": amount},
            "$setOnInsert": {"holds": {}, "threshold_flags": {}, "created_at": now},
            "$set": {"updated_at": now},
        },
        upsert=True,
    )
    ledger_row = {
        "_id": idempotency_key, "user_id": user_id, "kind": "grant",
        "bucket": bucket, "amount": amount, "source": source,
        "expires_at": expires_at, "created_at": now,
    }
    try:
        await db.wallet_ledger.insert_one(dict(ledger_row))
    except DuplicateKeyError:
        # Another caller already recorded this exact grant — reverse
        # OUR speculative $inc (the race's loser) and return the
        # winner's row so the net effect is "applied exactly once".
        await db.wallets.update_one(
            {"_id": user_id}, {"$inc": {f"buckets.{bucket}": -amount}},
        )
        existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
        return {"ok": True, "idempotent_replay": True, "ledger": existing}
    return {"ok": True, "idempotent_replay": False, "ledger": ledger_row}


async def hold(
    db, *, user_id: str, action: str, run_id: str, idempotency_key: str,
) -> dict:
    """Reserve `tokens_for(action)` tokens for `run_id`. Raises
    InsufficientTokens if the balance can't cover it. Idempotent on
    `idempotency_key` — a retry with the same key returns the
    ORIGINAL hold, never reserves twice."""
    existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if existing:
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "held": existing.get("amount"), "breakdown": existing.get("breakdown")}

    est_tokens = tokens_for(action)
    doc = await _atomic_bucket_spend(db, user_id, est_tokens)
    if doc is None or not doc.get("_ok"):
        bal = await get_balance(db, user_id)
        raise InsufficientTokens(user_id=user_id, needed=est_tokens, balance=bal["spendable"])

    breakdown = {
        "promo": doc["_take_promo"], "plan": doc["_take_plan"],
        "purchased": doc["_take_purchased"],
    }
    now = _now()
    await db.wallets.update_one(
        {"_id": user_id},
        {"$set": {f"holds.{run_id}": {
            "amount": est_tokens, "breakdown": breakdown,
            "action": action, "created_at": now,
        }}},
    )
    ledger_row = {
        "_id": idempotency_key, "user_id": user_id, "kind": "hold",
        "action": action, "run_id": run_id, "amount": est_tokens,
        "breakdown": breakdown, "created_at": now,
    }
    try:
        await db.wallet_ledger.insert_one(dict(ledger_row))
    except DuplicateKeyError:
        # Compensate: give the tokens back AND remove the holds-field
        # entry we just spuriously wrote, then defer to the winner.
        await db.wallets.update_one(
            {"_id": user_id},
            {"$inc": {f"buckets.{k}": v for k, v in breakdown.items()},
             "$unset": {f"holds.{run_id}": ""}},
        )
        existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "held": existing.get("amount"), "breakdown": existing.get("breakdown")}
    return {"ok": True, "idempotent_replay": False, "run_id": run_id,
             "held": est_tokens, "breakdown": breakdown}


async def _find_hold(db, user_id: str, run_id: str) -> dict | None:
    w = await db.wallets.find_one({"_id": user_id}, {f"holds.{run_id}": 1}) or {}
    return (w.get("holds") or {}).get(run_id)


async def settle(
    db, *, user_id: str, run_id: str, actual_tokens: int, idempotency_key: str,
) -> dict:
    """Finalize a hold at its REAL cost. Refunds the difference
    (pro-rata, into the same buckets the hold drew from) if
    actual_tokens < the held estimate. `actual_tokens` is capped at
    the held amount — a run can never end up costing the user MORE
    than what was reserved at hold()-time; any overage beyond the
    estimate is absorbed, not passed through. Idempotent on
    `idempotency_key`."""
    existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if existing:
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "charged": existing.get("amount")}

    h = await _find_hold(db, user_id, run_id)
    if h is None:
        # Already settled/released by a prior call with a DIFFERENT
        # idempotency_key (shouldn't happen for a well-behaved caller,
        # but fail loud rather than silently charging nothing).
        raise ValueError(f"no active hold for run_id={run_id!r} (already settled/released?)")

    held_amount = int(h["amount"])
    breakdown = h["breakdown"]
    actual = max(0, min(int(actual_tokens), held_amount))
    refund_total = held_amount - actual
    now = _now()

    if refund_total > 0:
        # Pro-rata refund back into the SAME buckets that were drawn
        # from — never launders an expiring "plan" spend into a
        # never-expiring "purchased" refund.
        remaining = refund_total
        refund_breakdown = {}
        for i, b in enumerate(SPEND_ORDER):
            taken = int(breakdown.get(b, 0))
            if taken <= 0:
                refund_breakdown[b] = 0
                continue
            if i == len(SPEND_ORDER) - 1:
                give = remaining
            else:
                give = min(taken, round(refund_total * taken / held_amount)) if held_amount else 0
                give = min(give, remaining)
            refund_breakdown[b] = give
            remaining -= give
        if remaining > 0:
            refund_breakdown[SPEND_ORDER[-1]] += remaining
        await db.wallets.update_one(
            {"_id": user_id},
            {"$inc": {f"buckets.{b}": v for b, v in refund_breakdown.items() if v}},
        )
    await db.wallets.update_one(
        {"_id": user_id}, {"$unset": {f"holds.{run_id}": ""}},
    )
    ledger_row = {
        "_id": idempotency_key, "user_id": user_id, "kind": "settle",
        "run_id": run_id, "amount": actual, "held_amount": held_amount,
        "refunded": refund_total, "created_at": now,
    }
    try:
        await db.wallet_ledger.insert_one(dict(ledger_row))
    except DuplicateKeyError:
        # Compensate: undo the refund we just sped-applied (if any) —
        # the hold entry is already gone either way (idempotent unset).
        if refund_total > 0:
            await db.wallets.update_one(
                {"_id": user_id},
                {"$inc": {f"buckets.{b}": -v for b, v in refund_breakdown.items() if v}},
            )
        existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "charged": existing.get("amount")}
    return {"ok": True, "idempotent_replay": False, "run_id": run_id,
             "charged": actual, "refunded": refund_total}


async def release(db, *, user_id: str, run_id: str, idempotency_key: str, reason: str = "") -> dict:
    """Full refund of an in-progress hold — the run failed or was
    cancelled before it could settle. Idempotent on `idempotency_key`;
    releasing a hold that's already been settled/released elsewhere is
    a safe no-op (returns held=0)."""
    existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if existing:
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "released": existing.get("amount")}

    h = await _find_hold(db, user_id, run_id)
    now = _now()
    if h is None:
        # Nothing to release — record a zero-amount ledger row so a
        # retry of THIS idempotency_key still replays cleanly, but
        # don't touch any buckets.
        ledger_row = {"_id": idempotency_key, "user_id": user_id, "kind": "release",
                       "run_id": run_id, "amount": 0, "reason": reason,
                       "note": "no_active_hold", "created_at": now}
        try:
            await db.wallet_ledger.insert_one(dict(ledger_row))
        except DuplicateKeyError:
            pass
        return {"ok": True, "idempotent_replay": False, "run_id": run_id, "released": 0}

    breakdown = h["breakdown"]
    held_amount = int(h["amount"])
    await db.wallets.update_one(
        {"_id": user_id},
        {"$inc": {f"buckets.{b}": v for b, v in breakdown.items() if v},
         "$unset": {f"holds.{run_id}": ""}},
    )
    ledger_row = {
        "_id": idempotency_key, "user_id": user_id, "kind": "release",
        "run_id": run_id, "amount": held_amount, "reason": reason, "created_at": now,
    }
    try:
        await db.wallet_ledger.insert_one(dict(ledger_row))
    except DuplicateKeyError:
        await db.wallets.update_one(
            {"_id": user_id}, {"$inc": {f"buckets.{b}": -v for b, v in breakdown.items() if v}},
        )
        existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "released": existing.get("amount")}
    return {"ok": True, "idempotent_replay": False, "run_id": run_id, "released": held_amount}


# ── B4 — the ONE mode-aware entry point callers use ─────────────────────

async def assert_wallet(db, *, user_id: str, action: str, run_id: str, idempotency_key: str) -> dict:
    """off    -> pure no-op, no tokens move.
    shadow -> computes/logs what WOULD happen, never calls hold(), never raises.
    on     -> raises a typed 402 if insufficient, else calls hold() for real.

    NEVER call this for: viewing results/issues, viewing a PR,
    rollback, export, login, billing — those code paths simply don't
    invoke it."""
    mode = billing_mode()
    if mode == "off":
        return {"mode": "off", "held": False}

    needed = tokens_for(action)
    bal = await get_balance(db, user_id)
    would_block = bal["spendable"] < needed

    if mode == "shadow":
        if would_block:
            logger.info(
                "[wallet-shadow] user=%s action=%s WOULD block — "
                "balance=%s needed=%s", user_id, action, bal["spendable"], needed,
            )
        else:
            logger.debug(
                "[wallet-shadow] user=%s action=%s would charge %s tokens "
                "(balance=%s)", user_id, action, needed, bal["spendable"],
            )
        return {"mode": "shadow", "held": False, "would_block": would_block, "balance": bal}

    # mode == "on"
    if would_block:
        from fastapi import HTTPException
        raise HTTPException(402, detail={
            "code": "tokens_exhausted",
            "balance": bal["spendable"],
            "refill_date": bal.get("plan_refill_at"),
            "recharge_url": "/settings#pricing",
        })
    hold_result = await hold(db, user_id=user_id, action=action, run_id=run_id,
                              idempotency_key=idempotency_key)
    return {"mode": "on", "held": True, "hold": hold_result, "balance": bal}


# ── B6 — monthly plan-token grant job (idempotent per user per month) ──

async def run_monthly_grant_job(db, *, dry_run: bool = False) -> dict:
    """Refill every paying user's `plan` bucket to their tier's monthly
    token allotment, and EXPIRE whatever unused `plan` tokens remain
    from last month (plan tokens don't roll over — `purchased` is
    untouched either way). Idempotent per (user_id, year-month): a
    second run in the same month for the same user is a no-op, keyed
    by `wallet_ledger._id = f"monthly_grant:{user_id}:{ym}"`.

    dry_run=True computes and returns what WOULD happen without
    writing anything — used by the Step G migration script and by
    ops to preview before a real run.
    """
    from config.token_pricing import (
        tokens_for as _tf,  # noqa: F401 (kept for future per-tier pricing)
    )
    ym = _year_month()
    db_cursor = db.dev_users.find(
        {"tier": {"$in": ["starter", "pro", "team"]}},
        {"_id": 0, "user_id": 1, "tier": 1},
    )
    plan_token_grants = {"starter": 900, "pro": 1900, "team": 4900}
    planned: list[dict] = []
    applied: list[dict] = []
    async for u in db_cursor:
        user_id = u.get("user_id")
        tier = u.get("tier")
        amount = plan_token_grants.get(tier)
        if not user_id or not amount:
            continue
        key = f"monthly_grant:{user_id}:{ym}"
        already = await db.wallet_ledger.find_one({"_id": key})
        planned.append({"user_id": user_id, "tier": tier, "amount": amount,
                          "already_granted": bool(already)})
        if dry_run or already:
            continue
        # Expire remainder of last month's plan bucket (replace, don't add).
        await db.wallets.update_one(
            {"_id": user_id},
            {"$set": {"buckets.plan": 0, "plan_refill_at": _now(),
                       "month_baseline": {"ym": ym},
                       "threshold_flags": {"ym": ym, "warn": False,
                                            "critical": False, "stop": False}},
             "$setOnInsert": {"holds": {}, "created_at": _now()}},
            upsert=True,
        )
        res = await grant(db, user_id=user_id, bucket="plan", amount=amount,
                           idempotency_key=key, source=f"monthly_grant:{tier}")
        bal = await get_balance(db, user_id)
        await db.wallets.update_one(
            {"_id": user_id},
            {"$set": {"month_baseline.amount": bal["spendable"]}},
        )
        applied.append({"user_id": user_id, "tier": tier, "amount": amount, "result": res})
    return {"year_month": ym, "dry_run": dry_run,
             "planned_count": len(planned), "applied_count": len(applied),
             "planned": planned, "applied": applied}


# ── B7 — balance thresholds (20% warn / 5% critical / 0% stop), once/month ──

THRESHOLD_WARN_PCT = 20.0
THRESHOLD_CRITICAL_PCT = 5.0


async def check_thresholds(db, user_id: str) -> dict:
    """Call after any spend. Fires each level (warn/critical/stop) AT
    MOST ONCE per calendar month per user — state stored in
    `wallets.threshold_flags`. `base` is the balance captured at the
    start of the month (set by run_monthly_grant_job); if no baseline
    exists yet (e.g. a purchased-only user with no plan), falls back
    to the CURRENT spendable balance the first time this is called
    (so a top-up-only user still gets a sane 100% starting point)."""
    ym = _year_month()
    w = await db.wallets.find_one({"_id": user_id}) or {}
    flags = w.get("threshold_flags") or {}
    if flags.get("ym") != ym:
        flags = {"ym": ym, "warn": False, "critical": False, "stop": False}

    baseline = (w.get("month_baseline") or {})
    base = baseline.get("amount") if baseline.get("ym") == ym else None
    bal = await get_balance(db, user_id)
    spendable = bal["spendable"]
    if base is None or base <= 0:
        base = max(spendable, 1)
        await db.wallets.update_one(
            {"_id": user_id},
            {"$set": {"month_baseline": {"ym": ym, "amount": base}}},
        )

    pct_left = round((spendable / base) * 100, 1) if base else 0.0
    newly_fired = []
    updates = {}
    if spendable <= 0 and not flags.get("stop"):
        newly_fired.append("stop")
        updates["threshold_flags.stop"] = True
    if pct_left <= THRESHOLD_CRITICAL_PCT and not flags.get("critical"):
        newly_fired.append("critical")
        updates["threshold_flags.critical"] = True
    if pct_left <= THRESHOLD_WARN_PCT and not flags.get("warn"):
        newly_fired.append("warn")
        updates["threshold_flags.warn"] = True
    if updates:
        updates["threshold_flags.ym"] = ym
        await db.wallets.update_one({"_id": user_id}, {"$set": updates}, upsert=True)

    return {
        "user_id": user_id, "pct_left": pct_left, "spendable": spendable,
        "base": base, "newly_fired": newly_fired,
        "state": {
            "warn": flags.get("warn") or "warn" in newly_fired,
            "critical": flags.get("critical") or "critical" in newly_fired,
            "stop": flags.get("stop") or "stop" in newly_fired,
        },
    }


# ── B8 — reconciliation report (read-only) ──────────────────────────────

async def reconcile_report(db, *, drift_alert_pct: float = 3.0) -> dict:
    """wallet_ledger spend totals vs services.loop_token_ledger's
    `real_llm_usage` provider-reported cost, converted to tokens via
    USD_TO_TOKENS, for the current UTC month. Read-only — never writes
    anything. Flags per-user drift above `drift_alert_pct`."""
    from config.token_pricing import usd_to_tokens
    month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    ledger_agg = await db.wallet_ledger.aggregate([
        {"$match": {"kind": "settle", "created_at": {"$gte": month_start}}},
        {"$group": {"_id": "$user_id", "wallet_tokens": {"$sum": "$amount"}}},
    ]).to_list(None)
    wallet_by_user = {r["_id"]: r["wallet_tokens"] for r in ledger_agg}

    provider_agg = await db.real_llm_usage.aggregate([
        {"$match": {"ts": {"$gte": month_start.timestamp()}}},
        {"$group": {"_id": "$user_id", "cost_usd": {"$sum": "$cost_usd"}}},
    ]).to_list(None)
    provider_by_user = {r["_id"]: usd_to_tokens(r.get("cost_usd") or 0) for r in provider_agg}

    all_users = set(wallet_by_user) | set(provider_by_user)
    rows = []
    flagged = []
    for u in all_users:
        w_tok = wallet_by_user.get(u, 0)
        p_tok = provider_by_user.get(u, 0)
        denom = max(w_tok, p_tok, 1)
        drift_pct = round(abs(w_tok - p_tok) / denom * 100, 2)
        row = {"user_id": u, "wallet_tokens": w_tok, "provider_tokens": p_tok,
                "drift_pct": drift_pct}
        rows.append(row)
        if drift_pct > drift_alert_pct:
            flagged.append(row)

    return {
        "month": _year_month(), "users_compared": len(rows),
        "flagged_count": len(flagged), "flagged": flagged, "rows": rows,
    }
