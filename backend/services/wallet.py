"""
services/wallet.py — Master Prompt Step B: token wallet (grant / hold /
settle / release / balance), gated by TOKEN_BILLING_MODE.

Buckets (stored on `wallets.buckets`, one doc per user, `_id = user_id`):
  - promo:     promotional tokens. May carry a per-grant `expires_at`.
  - plan:      monthly subscription allotment. Master Prompt v2 Part 2
               rollover — NEVER expires by default; run_monthly_grant_job
               ADDS each month's allotment on top of whatever's left
               (see B6), exactly like `purchased`. Only swept if the
               operator sets TOKEN_EXPIRY_MONTHS (services.pricing_engine),
               which defaults to unset/never.
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

Master Prompt v2 Part 2 — FIFO batches. Every `grant()` call also
appends one entry to `wallets.batches` (`{batch_id, bucket,
amount_remaining, created_at}`, `batch_id` = the grant's own
idempotency_key, so a batch is directly addressable by the payment/job
that created it). `hold()` additionally consumes the OLDEST batch(es)
within each bucket it drew from (`_consume_batches_fifo`), so a later
refund/clawback (`claw_back_batch`) can target the EXACT payment's
batch instead of the bucket in aggregate. This batch-level bookkeeping
is a best-effort OPTIMISTIC-CONCURRENCY read-modify-write (bounded
retries on a `batches_version` counter) layered ON TOP of the
already-atomic bucket spend above — a race here can only misattribute
which batch loses tokens (floored at 0, never negative), it can never
make the AGGREGATE balance wrong, because the aggregate is still
decremented by the fully-atomic pipeline update first. `claw_back_batch`
itself (the operation that actually gives money back) IS fully atomic —
it targets one named batch via Mongo's positional `$` update operator
in a single `update_one` call, no read-modify-write involved.

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
import math
import os
from datetime import UTC, datetime, timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from config.token_pricing import tokens_for

logger = logging.getLogger("aurem.wallet")

BUCKETS = ("promo", "plan", "purchased")
SPEND_ORDER = ("promo", "plan", "purchased")
_VALID_MODES = ("off", "shadow", "on")

# PRE-STEP (a) self-red-team — a sane ceiling on a single run's real
# cost (settle()'s actual_cost_usd). No genuine LLM call could ever
# legitimately approach this; it exists purely to stop a garbage or
# malicious value (1e18, +inf) from producing a token count that
# overflows Mongo's 8-byte int64 limit when persisted.
_MAX_SANE_COST_USD = 1_000_000.0

# PRE-STEP (a) continuation (Master Prompt v2) — ROUNDING LEAK fix.
# 1 token = 1000 micro-tokens. `wallets.buckets.{bucket}` stays a
# WHOLE-token integer (every existing grant/hold/flat-settle/release/
# claw_back flow is untouched, still exact at whole-token resolution —
# this is why none of those call sites or their tests needed to
# change). A NEW `wallets.micro_remainder.{bucket}` field (always in
# [0, 999], default 0) carries the EXACT sub-1-token remainder that
# only the actual_cost_usd settle() path can produce. The two together
# (`buckets.{b} * WALLET_MICRO_PER_TOKEN + micro_remainder.{b}`) are
# the TRUE micro-token balance at all times — `get_balance()`'s legacy
# integer fields are a ROUNDED display of that; the deduction itself
# (via `_apply_micro_refund`) is always exact, never rounded.
WALLET_MICRO_PER_TOKEN = 1000

# Single source of truth for the monthly token allotment per paid plan
# (fix list Tier 2 #17, C3). Both the Stripe webhook
# (routers/payments.py::stripe_webhook) and run_monthly_grant_job below
# use this mapping; do not duplicate these values elsewhere.
plan_token_grants = {"starter": 900, "pro": 1900, "team": 4900}


def _require_str(name: str, value) -> None:
    """PRE-STEP (a) self-red-team hardening — every one of these
    parameters is used as a literal Mongo `_id`/field-equality value in
    a QUERY FILTER (e.g. `find_one({"_id": idempotency_key})`), not
    just in an insert. A dict shaped like a Mongo operator
    (`{"$ne": "x"}`) passed as one of these is NOT rejected by Mongo on
    insert (Mongo allows arbitrary BSON as `_id`), but WOULD be
    structurally interpreted as a live operator the moment it's used
    as a query filter value — e.g. turning the idempotency dedup check
    into "find any row whose _id isn't X", matching an unrelated row.
    No current call site in this codebase passes anything but an
    f-string-built str here (which can never produce a dict), but this
    guard makes that an enforced invariant rather than an unenforced
    convention."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}: {value!r}")


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
    simply has zero everywhere.

    PRE-STEP (a) continuation — `promo`/`plan`/`purchased`/`spendable`
    are a ROUNDED DISPLAY of the true balance (exact for every legacy
    whole-token flow, since those never touch `micro_remainder` at
    all). `*_micro`/`*_micro_remainder`/`spendable_micro` are the
    EXACT values, including any sub-1-token remainder from the
    actual_cost_usd settle() path — use these wherever exactness
    (not display) matters."""
    w = await db.wallets.find_one({"_id": user_id}) or {}
    buckets = w.get("buckets") or {}
    remainder = w.get("micro_remainder") or {}
    bucket_promo = int(buckets.get("promo") or 0)
    bucket_plan = int(buckets.get("plan") or 0)
    bucket_purchased = int(buckets.get("purchased") or 0)
    r_promo = int(remainder.get("promo") or 0)
    r_plan = int(remainder.get("plan") or 0)
    r_purchased = int(remainder.get("purchased") or 0)
    promo_micro = bucket_promo * WALLET_MICRO_PER_TOKEN + r_promo
    plan_micro = bucket_plan * WALLET_MICRO_PER_TOKEN + r_plan
    purchased_micro = bucket_purchased * WALLET_MICRO_PER_TOKEN + r_purchased
    # Legacy display fields: round the TRUE (remainder-inclusive) value
    # to the nearest whole token. Exact (zero rounding error) for every
    # legacy flow, since remainder is always 0 there — only differs
    # from the raw bucket integer once a fractional settle has left a
    # real remainder behind.
    promo = round(promo_micro / WALLET_MICRO_PER_TOKEN)
    plan = round(plan_micro / WALLET_MICRO_PER_TOKEN)
    purchased = round(purchased_micro / WALLET_MICRO_PER_TOKEN)
    return {
        "user_id": user_id,
        "promo": promo, "plan": plan, "purchased": purchased,
        "spendable": promo + plan + purchased,
        "promo_micro_remainder": r_promo,
        "plan_micro_remainder": r_plan,
        "purchased_micro_remainder": r_purchased,
        "promo_micro": promo_micro, "plan_micro": plan_micro, "purchased_micro": purchased_micro,
        "spendable_micro": promo_micro + plan_micro + purchased_micro,
        "holds_count": len(w.get("holds") or {}),
        "plan_refill_at": w.get("plan_refill_at"),
        "month_baseline": (w.get("month_baseline") or {}).get("amount"),
    }


async def _atomic_bucket_spend(
    db, user_id: str, amount: int, *, pending_key: str | None = None,
    pending_action: str | None = None, pending_run_id: str | None = None,
    pending_intent: dict | None = None,
) -> dict:
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
    if pending_key is not None:
        # Record the spend in the pending record in the SAME atomic
        # pipeline update that debits the buckets (Aurem Tier 1 #5).
        # The spend is conditional on _ok, so a failed spend leaves
        # the intent record untouched (the $cond else branch), and a
        # successful spend can never be recorded without the matching
        # pending entry: a crash after this update always leaves
        # status spent with the real breakdown, and a crash before it
        # always leaves status intent, so recovery can tell the two
        # apart and never refunds money that never moved, nor loses
        # money that did.
        intent = pending_intent or {}
        pipeline.append(
            {
                "$set": {
                    f"pending_finalizations.{pending_key}": {
                        "$cond": [
                            "$_ok",
                            {
                                "kind": "hold",
                                "status": "spent",
                                "action": pending_action,
                                "run_id": pending_run_id,
                                "amount": amount,
                                "breakdown": {
                                    "promo": "$_take_promo",
                                    "plan": "$_take_plan",
                                    "purchased": "$_take_purchased",
                                },
                                "batch_breakdown": {},
                                "created_at": intent.get("created_at"),
                                "claimed_at": intent.get("claimed_at"),
                            },
                            intent,
                        ]
                    }
                }
            }
        )

    doc = await db.wallets.find_one_and_update(
        {"_id": user_id}, pipeline,
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    return doc


def _version_match(version: int) -> dict:
    """Match filter for the batches_version optimistic-lock CAS. A
    wallet doc that has never had a batch mutation yet has NO
    `batches_version` field at all (not 0) — a plain equality match
    `{"batches_version": 0}` does NOT match a missing field in Mongo,
    which would make every first batch-mutation on an old/fresh wallet
    doc spuriously "lose the race" and exhaust retries. `$in` with
    `None` matches both an explicit null AND a missing field, so this
    treats "missing" as version 0 correctly."""
    if version:
        return {"batches_version": version}
    return {"batches_version": {"$in": [0, None]}}


async def _consume_batches_fifo(db, user_id: str, bucket: str, amount: int,
                                 *, max_retries: int = 5) -> dict[str, int]:
    """Best-effort FIFO batch-level consumption (oldest `created_at`
    first) within `bucket`, for `amount` tokens already validated and
    deducted from the AGGREGATE bucket by `_atomic_bucket_spend`. Returns
    `{batch_id: amount_taken}`. Optimistic-concurrency retry loop on
    `batches_version` — see module docstring for why this doesn't need
    to be as strictly atomic as the aggregate spend itself."""
    if amount <= 0:
        return {}
    for _ in range(max_retries):
        w = await db.wallets.find_one(
            {"_id": user_id}, {"batches": 1, "batches_version": 1},
        ) or {}
        batches = list(w.get("batches") or [])
        version = w.get("batches_version", 0)
        this_bucket = sorted(
            (b for b in batches if b.get("bucket") == bucket),
            key=lambda b: b.get("created_at") or _now(),
        )
        remaining = amount
        taken: dict[str, int] = {}
        for b in this_bucket:
            if remaining <= 0:
                break
            avail = max(0, int(b.get("amount_remaining") or 0))
            take = min(avail, remaining)
            if take <= 0:
                continue
            taken[b["batch_id"]] = take
            remaining -= take
        if not taken:
            return {}
        new_batches = [
            {**b, "amount_remaining": max(0, int(b.get("amount_remaining") or 0) - taken[b["batch_id"]])}
            if b.get("batch_id") in taken else b
            for b in batches
        ]
        res = await db.wallets.update_one(
            {"_id": user_id, **_version_match(version)},
            {"$set": {"batches": new_batches}, "$inc": {"batches_version": 1}},
        )
        if res.matched_count:
            return taken
        # Lost the race against a concurrent batch mutation — retry.
    logger.warning(
        "[wallet] _consume_batches_fifo exhausted retries user=%s "
        "bucket=%s amount=%s — aggregate balance is still correct, "
        "only per-batch attribution for this spend is lost", user_id, bucket, amount,
    )
    return {}


async def _restore_batches(db, user_id: str, per_batch: dict[str, int],
                            *, max_retries: int = 5) -> None:
    """Give tokens back to the exact batches `_consume_batches_fifo`
    took them from (release, or settle's partial refund). A batch_id
    that no longer exists is silently skipped — the aggregate bucket
    refund (done separately by the caller) is still correct either
    way, this only restores batch-level attribution precision."""
    per_batch = {k: v for k, v in per_batch.items() if v}
    if not per_batch:
        return
    for _ in range(max_retries):
        w = await db.wallets.find_one(
            {"_id": user_id}, {"batches": 1, "batches_version": 1},
        ) or {}
        batches = list(w.get("batches") or [])
        version = w.get("batches_version", 0)
        changed = False
        new_batches = []
        for b in batches:
            amt = per_batch.get(b.get("batch_id"))
            if amt:
                b = {**b, "amount_remaining": int(b.get("amount_remaining") or 0) + amt}
                changed = True
            new_batches.append(b)
        if not changed:
            return
        res = await db.wallets.update_one(
            {"_id": user_id, **_version_match(version)},
            {"$set": {"batches": new_batches}, "$inc": {"batches_version": 1}},
        )
        if res.matched_count:
            return
    logger.warning(
        "[wallet] _restore_batches exhausted retries user=%s — aggregate "
        "bucket refund already applied by the caller; only per-batch "
        "attribution for this restore is lost", user_id,
    )


async def _apply_micro_refund(db, user_id: str, refund_micro: dict[str, int]) -> None:
    """PRE-STEP (a) continuation — credits an EXACT micro-token refund
    (1 token = WALLET_MICRO_PER_TOKEN micro-tokens) back into
    `buckets.{b}` (whole tokens) + `micro_remainder.{b}` (the sub-1000
    leftover), normalizing any carry (remainder crossing 1000 -> +1
    whole token) in a SINGLE atomic Mongo pipeline-style update per
    bucket, so two concurrent fractional settles on the SAME wallet
    can never race on the carry (each `update_one` call with a
    pipeline body reads-and-writes the existing field value
    atomically, the same guarantee `_atomic_bucket_spend` already
    relies on elsewhere in this module).

    This is the ONLY function that ever writes `micro_remainder` —
    every other mutation (grant/hold/flat-settle/release/claw_back)
    moves whole tokens only and never touches it, which is exactly why
    none of those existing, already-tested code paths needed to
    change for this fix."""
    # A refund is a credit: it must never be negative. A negative
    # micro-token value here would flow straight into the Mongo
    # $mod / $floor arithmetic below, producing a NEGATIVE
    # micro_remainder (Mongo $mod keeps the dividend sign)
    # and debiting whole tokens from buckets -- the exact opposite
    # of a refund, breaking the ledger invariant that a refund only
    # ever credits tokens back. Reject it loudly, before ANY bucket
    # is touched, instead of writing a negative amount.
    for bucket, micro in refund_micro.items():
        if micro < 0:
            raise ValueError(
                f"micro refund must not be negative: bucket={bucket!r} micro={micro}"
            )
    nonzero = {b: v for b, v in refund_micro.items() if v}
    if not nonzero:
        return
    for bucket, micro in nonzero.items():
        await db.wallets.update_one(
            {"_id": user_id},
            [{"$set": {
                f"micro_remainder.{bucket}": {
                    "$mod": [{"$add": [{"$ifNull": [f"$micro_remainder.{bucket}", 0]}, micro]}, WALLET_MICRO_PER_TOKEN],
                },
                f"buckets.{bucket}": {
                    "$add": [
                        {"$ifNull": [f"$buckets.{bucket}", 0]},
                        {"$floor": {"$divide": [
                            {"$add": [{"$ifNull": [f"$micro_remainder.{bucket}", 0]}, micro]},
                            WALLET_MICRO_PER_TOKEN,
                        ]}},
                    ],
                },
                "updated_at": _now(),
            }}],
        )


async def grant(
    db, *, user_id: str, bucket: str, amount: int,
    idempotency_key: str, source: str, expires_at: datetime | None = None,
) -> dict:
    """Add `amount` tokens to `bucket`. Idempotent on `idempotency_key`."""
    _require_str("user_id", user_id)
    _require_str("idempotency_key", idempotency_key)
    if bucket not in BUCKETS:
        raise ValueError(f"unknown bucket {bucket!r} — expected one of {BUCKETS}")
    if amount <= 0:
        raise ValueError(f"grant amount must be positive, got {amount}")
    existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if existing:
        return {"ok": True, "idempotent_replay": True, "ledger": existing}

    now = _now()
    batch = {"batch_id": idempotency_key, "bucket": bucket,
              "amount_remaining": amount, "created_at": now}
    await db.wallets.update_one(
        {"_id": user_id},
        {
            "$inc": {f"buckets.{bucket}": amount},
            "$push": {"batches": batch},
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
        # OUR speculative $inc/$push (the race's loser) and return the
        # winner's row so the net effect is "applied exactly once".
        await db.wallets.update_one(
            {"_id": user_id},
            {"$inc": {f"buckets.{bucket}": -amount},
             "$pull": {"batches": {"batch_id": idempotency_key}}},
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
    _require_str("user_id", user_id)
    _require_str("run_id", run_id)
    _require_str("idempotency_key", idempotency_key)

    existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if existing:
        return {
            "ok": True,
            "idempotent_replay": True,
            "run_id": run_id,
            "held": existing.get("amount"),
            "breakdown": existing.get("breakdown"),
        }

    # Crash recovery, part 1 (Aurem Tier 1 #5): a previous attempt with
    # this same idempotency_key may have died after spending but before
    # its ledger row was written. Its pending record (see below) is
    # enough to finish that hold instead of spending the tokens a
    # second time. A result here means the hold is already complete;
    # None means nothing half-done exists and a fresh hold is safe.
    recovered = await _recover_interrupted_hold(
        db, user_id=user_id, idempotency_key=idempotency_key
    )
    if recovered is not None:
        return recovered

    est_tokens = tokens_for(action)
    now = _now()
    # Crash recovery, part 2: record the pending hold DURABLY BEFORE
    # any money moves. The record carries the full intent (action,
    # run_id, estimated amount) and is only created when no pending
    # record for this key exists yet, so resuming a crashed attempt
    # can never overwrite a record that already knows the spend
    # happened. _atomic_bucket_spend then flips this same record to
    # status spent (with the real breakdown) in the SAME atomic
    # pipeline update that debits the buckets, so the record can
    # never claim the spend did not happen after it did, and can
    # never claim it did before it did.
    pending_intent = {
        "kind": "hold",
        "status": "intent",
        "action": action,
        "run_id": run_id,
        "amount": est_tokens,
        "breakdown": {},
        "batch_breakdown": {},
        "created_at": now,
        "claimed_at": now,
    }
    await db.wallets.update_one(
        {
            "_id": user_id,
            f"pending_finalizations.{idempotency_key}": {"$exists": False},
        },
        {"$set": {f"pending_finalizations.{idempotency_key}": pending_intent}},
    )
    doc = await _atomic_bucket_spend(
        db, user_id, est_tokens,
        pending_key=idempotency_key,
        pending_action=action,
        pending_run_id=run_id,
        pending_intent=pending_intent,
    )
    if doc is None or not doc.get("_ok"):
        # Nothing was debited (the spend is conditional on _ok),
        # so the intent record is simply no longer needed.
        await _clear_pending_finalization(db, user_id, idempotency_key)
        bal = await get_balance(db, user_id)
        raise InsufficientTokens(user_id=user_id, needed=est_tokens, balance=bal["spendable"])
    breakdown = {
        "promo": doc["_take_promo"],
        "plan": doc["_take_plan"],
        "purchased": doc["_take_purchased"],
    }
    # FIFO batch-level attribution (Part 2) -- see _consume_batches_fifo.
    batch_breakdown: dict[str, int] = {}
    for b, amt in breakdown.items():
        if amt > 0:
            batch_breakdown.update(await _consume_batches_fifo(db, user_id, b, amt))
    # The hold entry and the pending record move to held together,
    # in one atomic update on the wallet document: a crash after
    # this point leaves a hold that reconcile_stale_holds can
    # complete from the pending record, and a crash before it leaves
    # a spent pending record it can roll back.
    await db.wallets.update_one(
        {"_id": user_id},
        {
            "$set": {
                f"holds.{run_id}": {
                    "amount": est_tokens,
                    "breakdown": breakdown,
                    "batch_breakdown": batch_breakdown,
                    "action": action,
                    "created_at": now,
                },
                f"pending_finalizations.{idempotency_key}": {
                    **pending_intent,
                    "status": "held",
                    "breakdown": breakdown,
                    "batch_breakdown": batch_breakdown,
                },
            }
        },
    )
    ledger_row = {
        "_id": idempotency_key,
        "user_id": user_id,
        "kind": "hold",
        "action": action,
        "run_id": run_id,
        "amount": est_tokens,
        "breakdown": breakdown,
        "created_at": now,
    }
    try:
        await db.wallet_ledger.insert_one(dict(ledger_row))
    except DuplicateKeyError:
        # A duplicate (concurrent retry or resumed attempt) already
        # won this idempotency_key and owns the hold. Give back ONLY
        # this attempt spend: the winning hold entry must stay, or
        # the winner ledger row would point at a hold that no longer
        # exists.
        await db.wallets.update_one(
            {"_id": user_id},
            {"$inc": {f"buckets.{k}": v for k, v in breakdown.items()}},
        )
        await _restore_batches(db, user_id, batch_breakdown)
        await _clear_pending_finalization(db, user_id, idempotency_key)
        existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
        if existing is None:
            await db.wallets.update_one(
                {"_id": user_id},
                {"$unset": {f"holds.{run_id}": ""}},
            )
            return {
                "ok": True,
                "idempotent_replay": True,
                "run_id": run_id,
                "held": est_tokens,
                "breakdown": breakdown,
            }
        return {
            "ok": True,
            "idempotent_replay": True,
            "run_id": run_id,
            "held": existing.get("amount"),
            "breakdown": existing.get("breakdown"),
        }
    await _clear_pending_finalization(db, user_id, idempotency_key)
    return {
        "ok": True,
        "idempotent_replay": False,
        "run_id": run_id,
        "held": est_tokens,
        "breakdown": breakdown,
    }


async def _claim_hold(db, user_id: str, run_id: str, *,
                       idempotency_key: str, claim_kind: str) -> dict | None:
    """Atomically READ-AND-REMOVE the hold in one Mongo operation —
    AND, in that SAME atomic operation, durably record its full
    contents into `wallets.pending_finalizations.{idempotency_key}`.

    PRE-STEP (a) self-red-team fix (Master Prompt v2 continuation) — a
    real, 100%-reproducible double-refund bug: the old two-step
    "find_one, then later update_one $unset" let settle() and
    release() each independently read the SAME still-present hold when
    called concurrently for the same run_id (different idempotency
    keys, so neither's own dedup caught it), and each applied its own
    refund on top of the other. `asyncio.gather(settle(...),
    release(...))` on the same hold reproduced a wallet-inflation bug
    100/100 trials before this fix (full held amount refunded by
    release() AND settle()'s own partial refund, both landing). A
    single atomic `find_one_and_update` with `$unset` means exactly
    ONE of two concurrent callers ever sees the hold; the other
    correctly sees `None` (already resolved) and takes its existing
    safe path (settle() raises, release() no-ops at amount=0) — see
    tests/test_regression_iter2026_11c_prestep_redteam_and_edge_cases.py.

    2026-11-12 — SECURITY AUDIT FINDING, P0 financial integrity: that
    fix closed the double-refund race but left a DIFFERENT, real gap —
    plain `$unset` DELETES the hold with nothing else written. If the
    process crashed between this call returning and the caller's
    subsequent bucket-credit + `wallet_ledger` insert (settle()'s
    refund application, release()'s full refund), the hold's amount/
    breakdown was gone forever with NO record anywhere it had ever
    existed — tokens silently lost. A retry of the SAME
    idempotency_key would find no ledger row, call this again, get
    `None` back (hold already gone), and — for release() — write a
    ZERO-amount "no_active_hold" ledger row, masking real token loss
    as an ordinary "nothing to do" outcome.

    Fix: the SAME atomic update that removes the hold now ALSO copies
    its full contents into `pending_finalizations.{idempotency_key}`
    in the SAME document, via a Mongo aggregation-pipeline update
    (same technique `_atomic_bucket_spend` already uses elsewhere in
    this file) — still a single-document operation, atomic on ANY
    Mongo topology, no multi-document transaction required. If the
    process dies right after this call returns, the claimed hold's
    data SURVIVES (relocated, not erased) in `pending_finalizations`,
    and `reconcile_stale_holds()` can finish the interrupted settle/
    release later using that exact same idempotency_key — no
    guessing, no double-crediting (the `wallet_ledger` dedup this
    module already relies on everywhere else still applies). The
    normal (non-crashed) path removes its own `pending_finalizations`
    entry once its ledger row is durably written — see settle()/
    release() below.
    """
    doc = await db.wallets.find_one_and_update(
        {"_id": user_id, f"holds.{run_id}": {"$exists": True}},
        [
            {"$set": {"_claimed_hold": f"$holds.{run_id}"}},
            {"$set": {
                f"pending_finalizations.{idempotency_key}": {
                    "$mergeObjects": [
                        "$_claimed_hold",
                        {"run_id": run_id, "claim_kind": claim_kind, "claimed_at": _now()},
                    ],
                },
            }},
            {"$unset": [f"holds.{run_id}", "_claimed_hold"]},
        ],
        return_document=ReturnDocument.BEFORE,
    )
    if doc is None:
        return None
    return (doc.get("holds") or {}).get(run_id)


async def _clear_pending_finalization(db, user_id: str, idempotency_key: str) -> None:
    """Cleanup only — removes the now-unneeded `pending_finalizations`
    entry once the operation it was protecting has durably completed
    (its `wallet_ledger` row exists). Never load-bearing for
    correctness: `reconcile_stale_holds()` already checks for an
    existing ledger row before acting on any entry it finds, so a
    leftover entry (e.g. this cleanup itself racing a crash) is
    harmless, just a no-op `pending_finalizations` document the next
    cleanup pass removes."""
    await db.wallets.update_one(
        {"_id": user_id}, {"$unset": {f"pending_finalizations.{idempotency_key}": ""}},
    )


async def _recover_interrupted_hold(db, *, user_id: str, idempotency_key: str) -> dict | None:
    """Finish a hold() that died after spending but before its ledger
    row was written, using only the pending record hold() wrote
    before any money moved (Aurem Tier 1 #5).

    Returns the completed hold result when the pending record shows
    a hold that can be finished, or None when there is nothing to
    finish (no pending record, an intent whose spend never happened,
    or a record that is not a hold at all), in which case the caller
    goes on with a fresh hold. This function never refunds and never
    spends: it only completes work the crashed attempt had already
    paid for, so calling it twice, or racing the original attempt,
    can only ever produce the one hold and the one ledger row the
    idempotency_key names. A stale record that cannot be completed
    is left for reconcile_stale_holds, which rolls it back after the
    grace period instead of completing it.
    """
    wallet = await db.wallets.find_one(
        {"_id": user_id}, {"pending_finalizations": 1, "holds": 1}
    )
    if wallet is None:
        return None
    pf = (wallet.get("pending_finalizations") or {}).get(idempotency_key)
    if not pf or pf.get("kind") != "hold":
        return None
    run_id = pf.get("run_id")
    if not run_id:
        return None
    ledger = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if ledger is not None:
        await _clear_pending_finalization(db, user_id, idempotency_key)
        return {
            "ok": True,
            "idempotent_replay": True,
            "run_id": run_id,
            "held": ledger.get("amount"),
            "breakdown": ledger.get("breakdown"),
        }
    held = (wallet.get("holds") or {}).get(run_id)
    if held is not None:
        held_amount = int(held.get("amount") or 0)
        held_breakdown = held.get("breakdown") or pf.get("breakdown") or {}
        ledger_row = {
            "_id": idempotency_key,
            "user_id": user_id,
            "kind": "hold",
            "action": held.get("action") or pf.get("action"),
            "run_id": run_id,
            "amount": held_amount,
            "breakdown": held_breakdown,
            "created_at": held.get("created_at") or _now(),
        }
        try:
            await db.wallet_ledger.insert_one(dict(ledger_row))
        except DuplicateKeyError:
            ledger = await db.wallet_ledger.find_one({"_id": idempotency_key})
            await _clear_pending_finalization(db, user_id, idempotency_key)
            return {
                "ok": True,
                "idempotent_replay": True,
                "run_id": run_id,
                "held": ledger.get("amount") if ledger else held_amount,
                "breakdown": ledger.get("breakdown") if ledger else held_breakdown,
            }
        await _clear_pending_finalization(db, user_id, idempotency_key)
        return {
            "ok": True,
            "idempotent_replay": True,
            "run_id": run_id,
            "held": held_amount,
            "breakdown": held_breakdown,
        }
    if pf.get("status") not in ("spent", "held"):
        # Intent only: the spend is recorded atomically with the
        # status change to spent, so an intent record means the
        # spend never happened and a fresh hold is safe.
        return None
    amount = int(pf.get("amount") or 0)
    if amount <= 0:
        await _clear_pending_finalization(db, user_id, idempotency_key)
        return None
    breakdown = pf.get("breakdown") or {}
    batch_breakdown = pf.get("batch_breakdown") or {}
    created_at = pf.get("created_at") or _now()
    await db.wallets.update_one(
        {"_id": user_id},
        {
            "$set": {
                f"holds.{run_id}": {
                    "amount": amount,
                    "breakdown": breakdown,
                    "batch_breakdown": batch_breakdown,
                    "action": pf.get("action"),
                    "created_at": created_at,
                },
                f"pending_finalizations.{idempotency_key}": {
                    **pf,
                    "status": "held",
                    "breakdown": breakdown,
                    "batch_breakdown": batch_breakdown,
                },
            }
        },
    )
    ledger_row = {
        "_id": idempotency_key,
        "user_id": user_id,
        "kind": "hold",
        "action": pf.get("action"),
        "run_id": run_id,
        "amount": amount,
        "breakdown": breakdown,
        "created_at": created_at,
    }
    try:
        await db.wallet_ledger.insert_one(dict(ledger_row))
    except DuplicateKeyError:
        ledger = await db.wallet_ledger.find_one({"_id": idempotency_key})
        await _clear_pending_finalization(db, user_id, idempotency_key)
        return {
            "ok": True,
            "idempotent_replay": True,
            "run_id": run_id,
            "held": ledger.get("amount") if ledger else amount,
            "breakdown": ledger.get("breakdown") if ledger else breakdown,
        }
    await _clear_pending_finalization(db, user_id, idempotency_key)
    return {
        "ok": True,
        "idempotent_replay": True,
        "run_id": run_id,
        "held": amount,
        "breakdown": breakdown,
    }


async def settle(
    db, *, user_id: str, run_id: str, idempotency_key: str,
    actual_tokens: int | None = None, actual_cost_usd: float | None = None,
) -> dict:
    """Finalize a hold. Refunds the difference (pro-rata, into the
    same buckets AND the same batches the hold drew from) if the final
    charge < the held estimate. The held amount is a hard CEILING
    either way — a run can never end up costing the user MORE than
    what was reserved at hold()-time; any overage is absorbed, not
    passed through. Idempotent on `idempotency_key`.

    Master Prompt v2 Part 2 — pass `actual_cost_usd` (the run's real
    measured LLM cost) instead of `actual_tokens` to charge via the
    real-cost equation (services.pricing_engine.tokens_for_actual_cost)
    rather than a flat caller-supplied number — PRICE_TABLE's held
    estimate becomes a guard rail/ceiling, not the charge itself, but
    (deliberately, for safety) still the MOST the user is ever charged.
    A real cost that comes in at >= SPIKE_GUARD_MULTIPLE times the held
    estimate's dollar-equivalent is flagged via
    services.llm_cost_breaker.flag_actual_cost_spike for admin review —
    a pure signal, it does not raise what's charged. Exactly one of
    `actual_tokens`/`actual_cost_usd` must be given."""
    _require_str("user_id", user_id)
    _require_str("run_id", run_id)
    _require_str("idempotency_key", idempotency_key)
    if (actual_tokens is None) == (actual_cost_usd is None):
        raise ValueError("settle() needs exactly one of actual_tokens/actual_cost_usd")

    existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if existing:
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "charged": existing.get("amount")}

    h = await _claim_hold(db, user_id, run_id, idempotency_key=idempotency_key, claim_kind="settle")
    if h is None:
        # concurrent release() just atomically claimed it first (see
        # _claim_hold's docstring).
        raise ValueError(f"no active hold for run_id={run_id!r} (already settled/released?)")

    held_amount = int(h["amount"])
    held_micro = held_amount * WALLET_MICRO_PER_TOKEN
    breakdown = h["breakdown"]
    batch_breakdown = h.get("batch_breakdown") or {}
    action = h.get("action")

    true_cost_usd: float | None = None
    computed_tokens_uncapped: int | None = None
    if actual_cost_usd is not None:
        from config.token_pricing import tokens_to_usd
        from services.llm_cost_breaker import flag_actual_cost_spike
        from services.pricing_engine import (
            SPIKE_GUARD_MULTIPLE,
            get_effective_pricing_version,
            micro_tokens_for_actual_cost,
            tokens_for_actual_cost,
        )
        # PRE-STEP (a) self-red-team fix — an unbounded actual_cost_usd
        # (garbage/malicious caller, 1e18, +inf) produced a token count
        # so large that storing it in computed_tokens_uncapped below
        # overflowed Mongo's 8-byte int64 limit and raised mid-settle.
        # NaN is treated as "no real cost" (0); anything else is clamped
        # to a generous-but-sane ceiling — no single real LLM call could
        # ever legitimately approach $1M.
        raw_cost = float(actual_cost_usd)
        true_cost_usd = 0.0 if math.isnan(raw_cost) else max(0.0, min(raw_cost, _MAX_SANE_COST_USD))
        version = await get_effective_pricing_version(db)
        margin = version["margin"]
        # PRE-STEP (a) ROUNDING LEAK fix (Master Prompt v2 continuation)
        # — the DEDUCTION uses the EXACT micro-token equation output,
        # never rounded to a whole token first. `tokens_for_actual_cost`
        # (whole-token rounded) is kept only for `computed_tokens_uncapped`
        # /the dashboard's ceiling-hit display, which is fine to round —
        # it's a report, not a charge.
        actual_micro = micro_tokens_for_actual_cost(true_cost_usd, margin=margin)
        computed_tokens_uncapped = tokens_for_actual_cost(true_cost_usd, margin=margin)
        estimated_cost_usd = tokens_to_usd(held_amount)
        if estimated_cost_usd > 0 and true_cost_usd >= SPIKE_GUARD_MULTIPLE * estimated_cost_usd:
            await flag_actual_cost_spike(
                db, user_id=user_id, action=action, run_id=run_id,
                estimated_cost_usd=estimated_cost_usd, actual_cost_usd=true_cost_usd,
            )
    else:
        # Legacy flat-token path — actual_tokens is WHOLE tokens, so
        # this is always an exact multiple of WALLET_MICRO_PER_TOKEN;
        # every existing caller/test of this path sees byte-identical
        # behavior to before this fix.
        actual_micro = max(0, int(actual_tokens)) * WALLET_MICRO_PER_TOKEN

    actual_micro = max(0, min(actual_micro, held_micro))
    refund_total_micro = held_micro - actual_micro
    actual = actual_micro // WALLET_MICRO_PER_TOKEN  # legacy whole-token field, floored (exact charge is in *_micro)
    refund_total = refund_total_micro // WALLET_MICRO_PER_TOKEN
    now = _now()

    refund_breakdown_micro: dict[str, int] = {}
    batch_refund: dict[str, int] = {}
    if refund_total_micro > 0:
        # Pro-rata refund back into the SAME buckets (exact, at micro
        # resolution via _apply_micro_refund) and, best-effort, the
        # SAME batches (Part 2, whole-token resolution only — batches
        # don't track a sub-token remainder, so a fractional settle's
        # remainder is attributed to the aggregate bucket only, never
        # to a specific batch; an accepted, documented simplification
        # consistent with the batch system's existing best-effort
        # status) — never launders an expiring spend into a never-
        # expiring refund, and never credits a refund to a payment it
        # didn't come from.
        remaining_micro = refund_total_micro
        for i, b in enumerate(SPEND_ORDER):
            taken = int(breakdown.get(b, 0))  # whole tokens this bucket contributed to the hold
            if taken <= 0:
                refund_breakdown_micro[b] = 0
                continue
            taken_micro = taken * WALLET_MICRO_PER_TOKEN
            if i == len(SPEND_ORDER) - 1:
                give = remaining_micro
            else:
                give = min(taken_micro, round(refund_total_micro * taken_micro / held_micro)) if held_micro else 0
                give = min(give, remaining_micro)
            refund_breakdown_micro[b] = give
            remaining_micro -= give
        if remaining_micro > 0:
            refund_breakdown_micro[SPEND_ORDER[-1]] += remaining_micro
        await _apply_micro_refund(db, user_id, refund_breakdown_micro)

        if batch_breakdown and held_amount:
            refund_whole_for_batches = refund_total_micro // WALLET_MICRO_PER_TOKEN
            if refund_whole_for_batches > 0:
                remaining_b = refund_whole_for_batches
                batch_items = list(batch_breakdown.items())
                for i, (bid, taken) in enumerate(batch_items):
                    if i == len(batch_items) - 1:
                        give = remaining_b
                    else:
                        give = min(taken, round(refund_whole_for_batches * taken / held_amount))
                        give = min(give, remaining_b)
                    if give:
                        batch_refund[bid] = give
                    remaining_b -= give
                await _restore_batches(db, user_id, batch_refund)
    # Hold already atomically removed by _claim_hold above.
    ledger_row = {
        "_id": idempotency_key, "user_id": user_id, "kind": "settle",
        "action": action, "run_id": run_id, "amount": actual, "held_amount": held_amount,
        "refunded": refund_total, "amount_micro": actual_micro,
        "refunded_micro": refund_total_micro, "actual_cost_usd": true_cost_usd,
        "computed_tokens_uncapped": computed_tokens_uncapped,
        "created_at": now,
    }
    try:
        await db.wallet_ledger.insert_one(dict(ledger_row))
    except DuplicateKeyError:
        # Compensate: undo the refund we just sped-applied (if any) —
        # the hold entry is already gone either way (idempotent unset).
        if refund_total_micro > 0:
            await _apply_micro_refund(
                db, user_id, {k: -v for k, v in refund_breakdown_micro.items() if v},
            )
            if batch_refund:
                await _restore_batches(db, user_id, {k: -v for k, v in batch_refund.items()})
        await _clear_pending_finalization(db, user_id, idempotency_key)
        existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "charged": existing.get("amount"), "charged_micro": existing.get("amount_micro")}
    await _clear_pending_finalization(db, user_id, idempotency_key)
    return {"ok": True, "idempotent_replay": False, "run_id": run_id,
             "charged": actual, "refunded": refund_total,
             "charged_micro": actual_micro, "refunded_micro": refund_total_micro}


async def release(db, *, user_id: str, run_id: str, idempotency_key: str,
                   reason: str = "", failed_run_cost: float | None = None) -> dict:
    """Full refund of an in-progress hold — the run failed or was
    cancelled before it could settle. Idempotent on `idempotency_key`;
    releasing a hold that's already been settled/released elsewhere is
    a safe no-op (returns held=0).

    Master Prompt v2 Part 2 — `failed_run_cost` is the REAL USD cost the
    platform incurred on this run even though it failed (an LLM call
    that burned real tokens before the run was abandoned). Never charges
    the user (the hold is still fully refunded either way) — it's
    recorded on the ledger row purely for margin/break-even reporting
    (Part 3's admin dashboard) and the rolling-cost job."""
    _require_str("user_id", user_id)
    _require_str("run_id", run_id)
    _require_str("idempotency_key", idempotency_key)
    existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if existing:
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "released": existing.get("amount")}

    h = await _claim_hold(db, user_id, run_id, idempotency_key=idempotency_key, claim_kind="release")
    now = _now()
    if h is None:
        # Nothing to release — either already settled/released under a
        # different idempotency_key, or a concurrent settle() just
        # atomically claimed it first (see _claim_hold's docstring).
        # Record a zero-amount ledger row so a retry of THIS
        # idempotency_key still replays cleanly, but don't touch any
        # buckets.
        ledger_row = {"_id": idempotency_key, "user_id": user_id, "kind": "release",
                       "run_id": run_id, "amount": 0, "reason": reason,
                       "failed_run_cost": failed_run_cost,
                       "note": "no_active_hold", "created_at": now}
        try:
            await db.wallet_ledger.insert_one(dict(ledger_row))
        except DuplicateKeyError:
            pass
        return {"ok": True, "idempotent_replay": False, "run_id": run_id, "released": 0}

    breakdown = h["breakdown"]
    batch_breakdown = h.get("batch_breakdown") or {}
    held_amount = int(h["amount"])
    # Hold already atomically removed by _claim_hold above.
    await db.wallets.update_one(
        {"_id": user_id},
        {"$inc": {f"buckets.{b}": v for b, v in breakdown.items() if v}},
    )
    if batch_breakdown:
        await _restore_batches(db, user_id, batch_breakdown)
    ledger_row = {
        "_id": idempotency_key, "user_id": user_id, "kind": "release",
        "run_id": run_id, "amount": held_amount, "reason": reason,
        "failed_run_cost": failed_run_cost, "created_at": now,
    }
    try:
        await db.wallet_ledger.insert_one(dict(ledger_row))
    except DuplicateKeyError:
        await db.wallets.update_one(
            {"_id": user_id}, {"$inc": {f"buckets.{b}": -v for b, v in breakdown.items() if v}},
        )
        if batch_breakdown:
            await _restore_batches(db, user_id, {k: -v for k, v in batch_breakdown.items()})
        await _clear_pending_finalization(db, user_id, idempotency_key)
        existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
        return {"ok": True, "idempotent_replay": True, "run_id": run_id,
                 "released": existing.get("amount")}
    await _clear_pending_finalization(db, user_id, idempotency_key)
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
        # 2026-11-09 — devil's-advocate audit: durable, queryable
        # counterpart to the log lines above, so shadow predictions
        # can be systematically diffed against the real task-based
        # gate (services.wallet_shadow::compare_shadow_vs_task_based_gate)
        # before ever considering a TOKEN_BILLING_MODE flip to "on".
        from services.wallet_shadow import log_shadow_decision
        await log_shadow_decision(
            db, user_id=user_id, action=action, needed_tokens=needed,
            would_block=would_block, spendable_balance=bal["spendable"],
        )
        return {"mode": "shadow", "held": False, "would_block": would_block, "balance": bal}

    # mode == "on"
    from fastapi import HTTPException
    if would_block:
        raise HTTPException(402, detail={
            "code": "tokens_exhausted",
            "balance": bal["spendable"],
            "refill_date": bal.get("plan_refill_at"),
            "recharge_url": "/settings#pricing",
        })
    # 2026-10-25 (ITEM 2, independent review) — the `would_block` check
    # above is a non-atomic, time-of-check snapshot; `hold()` below
    # does its own atomic compare-and-swap against the REAL current
    # balance. Under concurrency, another caller's hold can land in
    # the gap between the two, so `hold()` can still legitimately
    # raise InsufficientTokens even though this snapshot looked fine.
    # Every one of this function's 6 call sites is contracted to
    # expect "either a real hold or a clean HTTPException(402)," never
    # a raw wallet exception — catch it here, once, instead of at
    # every caller.
    try:
        hold_result = await hold(db, user_id=user_id, action=action, run_id=run_id,
                                  idempotency_key=idempotency_key)
    except InsufficientTokens as e:
        raise HTTPException(402, detail={
            "code": "tokens_exhausted",
            "balance": e.balance,
            "refill_date": bal.get("plan_refill_at"),
            "recharge_url": "/settings#pricing",
        }) from e
    return {"mode": "on", "held": True, "hold": hold_result, "balance": bal}


async def finalize_hold(db, *, user_id: str, action: str, run_id: str,
                         ok: bool, reason: str = "",
                         actual_cost_usd: float | None = None) -> None:
    """Master Prompt v2 Part 1.1 — the ONE settle-or-release call every
    billable path (bulk fix, single-finding fix, chat fix, loop run,
    the single-task worker) makes once its run's outcome is known:
    settle() on success, release() on anything else.

    Master Prompt v2 Part 2 — if the caller has a real measured LLM
    cost for this run, pass it as `actual_cost_usd` and settle()/
    release() will charge/record via the real-cost equation instead of
    the flat PRICE_TABLE[action] guess (release() just records it as
    `failed_run_cost` — the user is never charged for a failed run
    either way). Omitted by default: no caller currently has per-run
    cost attribution plumbed through to this call site yet, so the
    flat-estimate behavior is unchanged unless a caller opts in.

    Mirrors assert_wallet's own off/shadow/on awareness: "off" and
    "shadow" never called hold() in the first place (assert_wallet's
    own no-op/log-only paths), so there is nothing here to settle or
    release either — attempting it would raise (settle() has no hold
    to find). Never raises; a billing bookkeeping failure must never
    mask the real result of the run it's describing."""
    if billing_mode() in ("off", "shadow") or not user_id:
        return
    try:
        if ok:
            if actual_cost_usd is not None:
                await settle(
                    db, user_id=user_id, run_id=run_id,
                    actual_cost_usd=actual_cost_usd,
                    idempotency_key=f"wallet_settle:{run_id}",
                )
            else:
                await settle(
                    db, user_id=user_id, run_id=run_id,
                    actual_tokens=tokens_for(action),
                    idempotency_key=f"wallet_settle:{run_id}",
                )
        else:
            await release(
                db, user_id=user_id, run_id=run_id,
                idempotency_key=f"wallet_release:{run_id}", reason=reason,
                failed_run_cost=actual_cost_usd,
            )
    except Exception as e:                                    # noqa: BLE001
        logger.warning(
            "[wallet] finalize_hold failed user=%s run_id=%s ok=%s: %r",
            user_id, run_id, ok, e,
        )


async def claw_back_batch(db, *, user_id: str, batch_id: str, amount: int,
                          idempotency_key: str, source: str) -> dict:
    """Master Prompt v2 Part 2 — refund/dispute clawback, precise to
    the ONE payment that created `batch_id` (the batch's id is the
    original grant's own idempotency_key — see module docstring).
    Removes up to `amount` tokens from that SPECIFIC batch, CAPPED at
    whatever is still remaining in it — never below zero, and never
    touches any other batch or bucket (a refund of one top-up must
    never eat into the user's plan tokens, or even a DIFFERENT top-up's
    batch). If the user already spent some or all of that batch, the
    shortfall is returned (not silently absorbed) so the caller can
    flag it for admin review — this function does not decide that, it
    just reports the gap honestly. Fully atomic — targets the one named
    batch via Mongo's positional `$` operator in a single `update_one`
    call (unlike the FIFO consume/restore helpers above, this needs no
    optimistic-retry loop because it isn't choosing WHICH batch to
    touch, the caller already supplied the exact one).

    `batch_id` not found (predates Part 2's batch tracking, or was
    never granted) — the full `amount` is reported as shortfall, no
    bucket is touched.

    Idempotent on `idempotency_key` via the same ledger-row pattern
    every other wallet mutation uses."""
    _require_str("user_id", user_id)
    _require_str("batch_id", batch_id)
    _require_str("idempotency_key", idempotency_key)
    existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
    if existing:
        return {"ok": True, "idempotent_replay": True,
                 "removed": existing.get("amount"), "shortfall": existing.get("shortfall", 0)}

    w = await db.wallets.find_one({"_id": user_id}) or {}
    batch = next(
        (b for b in (w.get("batches") or []) if b.get("batch_id") == batch_id), None,
    )
    if batch is None:
        ledger_row = {
            "_id": idempotency_key, "user_id": user_id, "kind": "refund_clawback",
            "batch_id": batch_id, "amount": 0, "shortfall": int(amount),
            "source": source, "created_at": _now(),
        }
        try:
            await db.wallet_ledger.insert_one(dict(ledger_row))
        except DuplicateKeyError:
            existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
            return {"ok": True, "idempotent_replay": True,
                     "removed": existing.get("amount"), "shortfall": existing.get("shortfall", 0)}
        return {"ok": True, "idempotent_replay": False, "removed": 0, "shortfall": int(amount)}

    bucket = batch["bucket"]
    current = int(batch.get("amount_remaining") or 0)
    to_remove = max(0, min(int(amount), current))
    shortfall = max(0, int(amount) - to_remove)

    if to_remove > 0:
        await db.wallets.update_one(
            {"_id": user_id, "batches.batch_id": batch_id},
            {"$inc": {f"buckets.{bucket}": -to_remove,
                       "batches.$.amount_remaining": -to_remove,
                       "batches_version": 1}},
        )
    ledger_row = {
        "_id": idempotency_key, "user_id": user_id, "kind": "refund_clawback",
        "batch_id": batch_id, "bucket": bucket, "amount": to_remove,
        "shortfall": shortfall, "source": source, "created_at": _now(),
    }
    try:
        await db.wallet_ledger.insert_one(dict(ledger_row))
    except DuplicateKeyError:
        if to_remove > 0:
            await db.wallets.update_one(
                {"_id": user_id, "batches.batch_id": batch_id},
                {"$inc": {f"buckets.{bucket}": to_remove,
                           "batches.$.amount_remaining": to_remove,
                           "batches_version": 1}},
            )
        existing = await db.wallet_ledger.find_one({"_id": idempotency_key})
        return {"ok": True, "idempotent_replay": True,
                 "removed": existing.get("amount"), "shortfall": existing.get("shortfall", 0)}
    return {"ok": True, "idempotent_replay": False, "removed": to_remove, "shortfall": shortfall}


# ── B6 — monthly plan-token grant job (idempotent per user per month) ──

async def _expire_old_batches(db, user_id: str, months: int) -> int:
    """Zero out (not delete — keep the audit trail) any batch whose
    created_at is older than `months` months, mirroring the amount back
    out of its parent bucket. No-op unless TOKEN_EXPIRY_MONTHS is
    configured (default: unset, so plan/purchased batches never expire
    — Part 2's rollover default). Runs from the serialized monthly cron,
    not under concurrent load, so a plain read-modify-write (no
    optimistic-lock retry) is fine here — unlike the hot hold()/settle()
    path."""
    cutoff = _now() - timedelta(days=months * 30)
    w = await db.wallets.find_one({"_id": user_id}, {"batches": 1}) or {}
    batches = list(w.get("batches") or [])
    by_bucket: dict[str, int] = {}
    new_batches = []
    for b in batches:
        created = b.get("created_at")
        remaining = int(b.get("amount_remaining") or 0)
        if remaining > 0 and created and created < cutoff:
            by_bucket[b["bucket"]] = by_bucket.get(b["bucket"], 0) + remaining
            b = {**b, "amount_remaining": 0}
        new_batches.append(b)
    expired_total = sum(by_bucket.values())
    if expired_total > 0:
        await db.wallets.update_one(
            {"_id": user_id},
            {"$set": {"batches": new_batches},
             "$inc": {f"buckets.{b}": -v for b, v in by_bucket.items()}},
        )
    return expired_total


async def run_monthly_grant_job(db, *, dry_run: bool = False) -> dict:
    """ADD each paying user's tier allotment to their `plan` bucket.

    Master Prompt v2 Part 2 — rollover: plan tokens no longer expire at
    the next refill. This job now ADDS the new month's allotment ON TOP
    of whatever's left (via `grant()`, same as any other grant),
    instead of replacing the bucket — matching `purchased`'s existing
    never-expire behavior. If `TOKEN_EXPIRY_MONTHS` is configured (env,
    default unset = never), batches older than that are swept to zero
    first via `_expire_old_batches`.

    Idempotent per (user_id, year-month): a second run in the same
    month for the same user is a no-op, keyed by
    `wallet_ledger._id = f"monthly_grant:{user_id}:{ym}"` — the SAME key
    `routers/payments.py`'s checkout.session.completed handler uses for
    a brand-new subscriber's instant first-month grant, so signing up
    mid-month can never ALSO get double-granted by this cron later that
    same month (a latent gap this unification closes).

    dry_run=True computes and returns what WOULD happen without
    writing anything — used by the Step G migration script and by
    ops to preview before a real run.

    PRE-STEP (b) [sic — see PART 3 pre-step] fix: gated on
    billing_mode() — "off" and "shadow" must be a COMPLETE no-op here,
    exactly like assert_wallet()/finalize_hold() elsewhere in this
    module. Before this fix the job had no mode check at all, so it
    would unconditionally create/populate a `wallets` document for
    every paying "starter"/"pro"/"team" user — including a pure
    task-based legacy user who has never touched token billing —
    violating "existing users' data untouched" the moment this cron
    ran with billing off (today's default).
    """
    mode = billing_mode()
    if mode in ("off", "shadow"):
        return {"year_month": _year_month(), "dry_run": dry_run,
                 "planned_count": 0, "applied_count": 0,
                 "planned": [], "applied": [], "skipped_reason": f"billing_mode_{mode}"}

    from services.pricing_engine import TOKEN_EXPIRY_MONTHS
    ym = _year_month()
    db_cursor = db.dev_users.find(
        {"tier": {"$in": ["starter", "pro", "team"]}},
        {"_id": 0, "user_id": 1, "tier": 1},
    )
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
        if TOKEN_EXPIRY_MONTHS:
            await _expire_old_batches(db, user_id, TOKEN_EXPIRY_MONTHS)
        await db.wallets.update_one(
            {"_id": user_id},
            {"$set": {"plan_refill_at": _now(),
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
            {"$set": {"month_baseline": {"ym": ym, "amount": bal["spendable"]}}},
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


# ── B8a — crash-safety reconciler for interrupted settle()/release() ───
# 2026-11-12 — security-audit finding (P0 financial integrity). See
# _claim_hold()'s docstring for the full story: a process crash
# between that atomic claim and the ledger write that was supposed to
# finish settle()/release() used to lose the held tokens with no
# trace. Every claim now durably survives as a
# `wallets.pending_finalizations.{idempotency_key}` entry until its
# operation completes; this function finds any that are older than
# `grace_seconds` with no matching `wallet_ledger` row (i.e. genuinely
# interrupted, not just "still running") and finishes them.

async def _reconcile_stale_hold(db, *, user_id: str, key: str, pf: dict) -> dict:
    """Resolve one stale hold pending record (Aurem Tier 1 #5).

    This is the recovery half of the hold crash window: a pending
    hold record that is older than the grace period belongs to an
    attempt that is not coming back, so reconcile_stale_holds
    finishes it one way or the other instead of leaving the money
    in limbo. The outcome depends on how far the attempt got:

    - the hold entry exists but its ledger row does not: complete
      the hold by writing the missing ledger row from the hold
      entry itself, so the hold and the ledger agree again;
    - only an intent was ever recorded: the spend is recorded
      atomically with the status change to spent, so an intent
      means the spend never happened and the record is dropped
      with nothing to refund;
    - the spend happened (status spent or held) but no hold entry
      exists: roll the spend back in full (buckets by breakdown,
      batches by batch_breakdown) and write no ledger row, so a
      later retry with the same idempotency_key is a fresh hold.
    """
    run_id = pf.get("run_id")
    amount = int(pf.get("amount") or 0)
    breakdown = dict(pf.get("breakdown") or {})
    batch_breakdown = dict(pf.get("batch_breakdown") or {})
    status = pf.get("status")
    base = {"user_id": user_id, "run_id": run_id, "amount": amount}
    wallet = await db.wallets.find_one({"_id": user_id}, {"holds": 1})
    held = ((wallet or {}).get("holds") or {}).get(run_id) if run_id else None
    if held is not None:
        held_amount = int(held.get("amount") or amount)
        held_breakdown = held.get("breakdown") or breakdown
        ledger_row = {
            "_id": key,
            "user_id": user_id,
            "kind": "hold",
            "action": held.get("action") or pf.get("action"),
            "run_id": run_id,
            "amount": held_amount,
            "breakdown": held_breakdown,
            "created_at": held.get("created_at") or _now(),
        }
        try:
            await db.wallet_ledger.insert_one(dict(ledger_row))
        except DuplicateKeyError:
            pass
        await _clear_pending_finalization(db, user_id, key)
        return {**base, "amount": held_amount, "note": "completed_hold"}
    if status == "intent" or amount <= 0:
        await _clear_pending_finalization(db, user_id, key)
        return {**base, "note": "discarded_unspent_hold_intent"}
    if breakdown:
        await db.wallets.update_one(
            {"_id": user_id},
            {"$inc": {f"buckets.{k}": v for k, v in breakdown.items()}},
        )
    if batch_breakdown:
        await _restore_batches(db, user_id, batch_breakdown)
    await _clear_pending_finalization(db, user_id, key)
    return {**base, "note": "rolled_back_interrupted_hold"}


async def reconcile_stale_holds(db, *, grace_seconds: int = 300) -> dict:
    """Completes any settle()/release() that claimed its hold but
    never finished writing its `wallet_ledger` row (the process died
    in between). Intended to run periodically (e.g. from the existing
    cron infrastructure), same spirit as `reconcile_report` above but
    this one WRITES — it is the actual recovery action, not just a
    report.

    Every interrupted operation (settle OR release) is resolved as a
    FULL refund of the held amount, using the idempotency_key the
    ORIGINAL caller already chose (recorded in the pending-
    finalization entry itself) — never a new decision, never a guess.
    This is safe specifically because a run that never finished
    settling is, by definition, a run whose outcome was never
    recorded — and this module's own hard ceiling already treats "the
    user is never charged more than the held estimate" as the
    universal safe default; an unresolved hold refunding in full is
    exactly that same default, just applied automatically instead of
    by an explicit release() call.

    Safe to call concurrently (multiple pods, or overlapping cron
    runs) and safe to call on a hold that ISN'T actually stuck: the
    `wallet_ledger.insert_one` dedup this module already relies on
    everywhere else is still the sole source of truth for "already
    credited" — if the original (slow, not actually crashed) caller
    finishes first, this function's own speculative credit is undone
    by the same `DuplicateKeyError` compensation pattern used
    throughout settle()/release()."""
    cutoff = _now() - timedelta(seconds=grace_seconds)
    resolved: list[dict] = []
    cursor = db.wallets.find(
        {"pending_finalizations": {"$exists": True, "$ne": {}}},
        {"pending_finalizations": 1},
    )
    async for w in cursor:
        user_id = w["_id"]
        for key, pf in (w.get("pending_finalizations") or {}).items():
            claimed_at = pf.get("claimed_at")
            if claimed_at and claimed_at.tzinfo is None:
                # Mongo round-trips a stored UTC datetime as naive by
                # default (this connection's tz_aware=False) — every
                # write here used _now() (always UTC), so a naive
                # read-back is always UTC, never ambiguous.
                claimed_at = claimed_at.replace(tzinfo=UTC)
            if not claimed_at or claimed_at > cutoff:
                continue  # too recent — the original caller may still be mid-flight
            existing = await db.wallet_ledger.find_one({"_id": key})
            if existing is not None:
                # The ledger write already succeeded — only this
                # now-stale bookkeeping entry's cleanup never ran.
                await _clear_pending_finalization(db, user_id, key)
                resolved.append({"user_id": user_id, "idempotency_key": key, "note": "cleanup_only"})
                continue

            if pf.get("kind") == "hold":
                resolved.append(
                    await _reconcile_stale_hold(db, user_id=user_id, key=key, pf=pf)
                )
                continue
            run_id = pf.get("run_id")
            breakdown = pf.get("breakdown") or {}
            batch_breakdown = pf.get("batch_breakdown") or {}
            held_amount = int(pf.get("amount") or 0)
            if held_amount > 0:
                await db.wallets.update_one(
                    {"_id": user_id},
                    {"$inc": {f"buckets.{b}": v for b, v in breakdown.items() if v}},
                )
                if batch_breakdown:
                    await _restore_batches(db, user_id, batch_breakdown)
            ledger_row = {
                "_id": key, "user_id": user_id, "kind": "release",
                "run_id": run_id, "amount": held_amount,
                "reason": "reconciled_stale_claim", "created_at": _now(),
            }
            try:
                await db.wallet_ledger.insert_one(dict(ledger_row))
            except DuplicateKeyError:
                # A concurrent caller (or another reconciler pass) beat
                # us to it — undo our speculative credit above.
                if held_amount > 0:
                    await db.wallets.update_one(
                        {"_id": user_id},
                        {"$inc": {f"buckets.{b}": -v for b, v in breakdown.items() if v}},
                    )
                    if batch_breakdown:
                        await _restore_batches(db, user_id, {k: -v for k, v in batch_breakdown.items()})
            await _clear_pending_finalization(db, user_id, key)
            resolved.append({"user_id": user_id, "idempotency_key": key,
                              "run_id": run_id, "amount": held_amount})
    return {"resolved": resolved, "grace_seconds": grace_seconds}


async def reconcile_stale_holds_cron(interval_seconds: int = 300, *,
                                      startup_delay_seconds: int = 60) -> None:
    """Background loop wiring for `reconcile_stale_holds()` — same
    `asyncio.create_task` + delay-then-loop-forever convention already
    used by `services/funnel_nudge_cron.py`'s `nudge_cron`. Runs every
    5 minutes by default; each tick's `grace_seconds=300` default
    inside `reconcile_stale_holds` means a claim only gets recovered
    once it's genuinely been sitting unfinished for 5+ minutes, never
    racing a caller that's still actually mid-flight."""
    import asyncio
    from cto_services.db import get_db
    await asyncio.sleep(startup_delay_seconds)
    while True:
        try:
            db = get_db()
            if db is not None:
                result = await reconcile_stale_holds(db)
                if result["resolved"]:
                    logger.info("💰 wallet reconcile_stale_holds — resolved=%d",
                                len(result["resolved"]))
        except Exception as e:
            logger.warning("reconcile_stale_holds_cron tick failed: %r", e)
        await asyncio.sleep(interval_seconds)


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
