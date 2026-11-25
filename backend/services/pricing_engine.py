"""
services/pricing_engine.py — Master Prompt v2 Part 2: the real charging
equation, versioned margin/fee config, and rolling real-cost tracking.

`config/token_pricing.py`'s PRICE_TABLE is a FLAT per-action estimate —
it's what a hold reserves up front, and it remains the charge whenever a
caller doesn't (yet) have a real per-run LLM cost to report. This module
is what lets a caller that DOES have a real cost (`services.wallet.settle`'s
new optional `actual_cost_usd` parameter) charge the user what the run
actually cost, instead of the flat guess — PRICE_TABLE becomes a floor/
guard-rail for the hold and for callers with no real-cost data, not the
charging mechanism itself.

The equation (wallet tokens are USD cents — see config.token_pricing.
USD_TO_TOKENS):

    wallet_tokens_charged = actual_llm_cost_usd * 100 / (1 - margin)

`margin` (and, for completeness, the Stripe processing fee the business
actually pays) live in `pricing_versions` — a tiny, append-only,
effective-dated collection so a margin change is auditable and never
rewrites history for runs already settled under a prior version.

Micro-tokens: the equation is computed in MICRO_TOKENS_PER_TOKEN-scaled
integer arithmetic before rounding to a whole token at the very end, so a
genuinely tiny real cost (a fraction of a cent) still rounds to >=1 token
rather than silently truncating to 0 — "the platform ate a real cost for
free" is a worse failure mode than "rounded a fraction of a cent up".
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import math
import os
import time
import uuid as _uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger("aurem.pricing_engine")

MICRO_TOKENS_PER_TOKEN: int = 1_000_000

# Used only when no `pricing_versions` row exists yet (fresh install /
# before an admin has set one via Part 3's margin editor) — an explicit,
# clearly-labelled placeholder, same convention as token_pricing.py's
# own PRICE_TABLE estimates.
DEFAULT_MARGIN: float = 0.50
DEFAULT_STRIPE_FEE_PCT: float = 0.029
DEFAULT_STRIPE_FEE_FIXED_USD: float = 0.30

# Master Prompt v2 Part 2 rollover — None (default) means plan/purchased
# batches never expire, matching today's "purchased never expires"
# behavior extended to "plan" too. Set to an integer to start expiring
# batches older than that many months (swept by the monthly grant job).
TOKEN_EXPIRY_MONTHS: int | None = (
    int(os.environ["TOKEN_EXPIRY_MONTHS"])
    if os.environ.get("TOKEN_EXPIRY_MONTHS", "").strip()
    else None
)

# 3x spike guard (Part 2) — how far actual cost may exceed the held
# estimate before wallet.settle() caps the charge and flags it via
# services.llm_cost_breaker instead of passing the full overage to the
# user.
SPIKE_GUARD_MULTIPLE: float = 3.0

# PART 3 — the admin-facing margin editor's business-rule bounds. Tighter
# than set_pricing_version()'s own data-model bound of [0, 1) (which
# stays permissive for internal/test/migration callers) — this is
# specifically what a client-submitted margin must satisfy.
MARGIN_MIN: float = 0.10
MARGIN_MAX: float = 0.80

_PREVIEW_TTL_SECONDS: int = 300


def usd_to_micro_tokens(usd: float) -> int:
    from config.token_pricing import USD_TO_TOKENS
    return round(usd * USD_TO_TOKENS * MICRO_TOKENS_PER_TOKEN)


def micro_tokens_to_tokens(micro_tokens: int) -> int:
    """Round to the NEAREST whole token — can legitimately be 0 for a
    genuinely tiny cost. PRE-STEP (b) correction (Master Prompt v2
    continuation): an earlier version forced a minimum of 1 token for
    any positive cost, reasoning that a $0 charge for real spend was
    worse than a tiny overcharge — but that silently overcharges a
    cheap action by orders of magnitude relative to what it actually
    cost. There is no accumulation of the rounded-away remainder across
    calls; `wallet.settle()`'s own `actual_cost_usd` ledger field still
    carries the TRUE cost for margin reporting regardless of how many
    tokens were actually collected from the user."""
    return max(0, round(micro_tokens / MICRO_TOKENS_PER_TOKEN))


def tokens_for_actual_cost(actual_cost_usd: float, *, margin: float) -> int:
    """wallet_tokens_charged = actual_llm_cost_usd * 100 / (1 - margin),
    rounded to the NEAREST whole token (never floored to a forced
    minimum — see `micro_tokens_to_tokens`). `margin` must be in
    [0, 1) — a margin of 1.0 or more would divide by zero or go
    negative, which means the caller passed a fraction instead of a
    percent (or a config bug); fail loud rather than silently charging
    an absurd amount.

    STALE-COMMENT FIX, PRE-STEP (a) continuation (Master Prompt v2) —
    this whole-token-rounded value is NO LONGER what services.wallet.
    settle() actually deducts from a user's balance (that was the
    ROUNDING LEAK: every call under ~$0.004 rounded to 0 tokens
    charged). settle() now calls `micro_tokens_for_actual_cost` below
    for the real, exact, never-rounded deduction; this function
    survives only for the dashboard's whole-token-ish display fields
    (`computed_tokens_uncapped`, `plan_capacity`) where rounding for
    a report is fine — it must never be used to compute a charge."""
    if actual_cost_usd <= 0:
        return 0
    if not (0 <= margin < 1):
        raise ValueError(f"margin must be in [0, 1), got {margin!r}")
    micro = usd_to_micro_tokens(actual_cost_usd) / (1 - margin)
    return micro_tokens_to_tokens(round(micro))


# PRE-STEP (a) continuation (Master Prompt v2) — the WALLET's own
# deduction precision. Distinct from MICRO_TOKENS_PER_TOKEN (1_000_000)
# above, which is this module's internal computation scale used only
# to avoid float error before a FINAL rounding step — this constant is
# the actual unit services.wallet stores and deducts in. 1 token =
# 1000 micro-tokens, as explicitly specified.
WALLET_MICRO_PER_TOKEN: int = 1000

# Same sane ceiling as services.wallet._MAX_SANE_COST_USD, duplicated
# here so this function is self-contained/safe even if called directly
# without going through wallet.settle()'s own clamp.
_MAX_SANE_COST_USD_FOR_MICRO: float = 1_000_000.0


def micro_tokens_for_actual_cost(actual_cost_usd: float, *, margin: float) -> int:
    """The EXACT wallet deduction, in micro-tokens (1 token = 1000
    micro-tokens) — never rounded to a whole token. This is what
    services.wallet.settle() actually deducts from the user's balance
    when a real cost is known; `tokens_for_actual_cost` above (whole-
    token rounded) exists only for legacy/display callers that
    predate this fix.

    PRE-STEP (a) ROUNDING LEAK fix (Master Prompt v2 continuation) —
    the previous "round to nearest whole token, allow 0" design meant
    every call costing under ~$0.004 (at a 20% margin) deducted ZERO
    tokens: a real, recurring free ride for high-frequency cheap
    actions (chat turns, classify, extract, summarize) while the
    platform still paid the real provider cost for every one of them.
    Only `services.wallet.get_balance()`'s legacy whole-token DISPLAY
    fields round now; the deduction itself is exact at micro-token
    resolution, with the sub-1000 remainder carried forward exactly
    (never dropped) via `services.wallet._apply_micro_refund`.

    margin must be in [0, 1) — see `tokens_for_actual_cost`."""
    if actual_cost_usd <= 0:
        return 0
    if not (0 <= margin < 1):
        raise ValueError(f"margin must be in [0, 1), got {margin!r}")
    from config.token_pricing import USD_TO_TOKENS
    cost = min(float(actual_cost_usd), _MAX_SANE_COST_USD_FOR_MICRO)
    micro = cost * USD_TO_TOKENS * WALLET_MICRO_PER_TOKEN / (1 - margin)
    return max(0, round(micro))


def plan_capacity(plan_tokens: int, action: str, *, margin: float | None = None) -> float | None:
    """Plan-level math helper — 'how many of `action` does a plan
    granting `plan_tokens`/month actually buy', using the REAL rolling
    average cost for that action when one has been computed (see
    `run_rolling_cost_update`), falling back to the flat PRICE_TABLE
    guess otherwise. Returns None if `action` isn't priced at all."""
    from config.token_pricing import PRICE_TABLE
    if action not in PRICE_TABLE:
        return None
    per_action_tokens = PRICE_TABLE[action]  # flat-estimate fallback
    # Callers that want the REAL-cost-driven number should pass the
    # rolling average in via `tokens_for_actual_cost` themselves and
    # call this with the resulting token price — kept as a pure, DB-
    # free helper so it's trivially testable; see `plan_capacity_live`
    # for the DB-backed version admin/Part 3 actually wants.
    if per_action_tokens <= 0:
        return None
    return round(plan_tokens / per_action_tokens, 1)


async def plan_capacity_live(db, plan_tokens: int, action: str) -> float | None:
    """DB-backed version of `plan_capacity` — prefers the real rolling
    7-day average cost for `action` (converted through the current
    pricing version's margin) over the flat PRICE_TABLE guess."""
    version = await get_effective_pricing_version(db)
    stats = await db.action_cost_stats.find_one({"_id": action})
    avg_cost_usd = (stats or {}).get("avg_cost_usd_7d")
    if avg_cost_usd:
        per_action_tokens = tokens_for_actual_cost(avg_cost_usd, margin=version["margin"])
        if per_action_tokens > 0:
            return round(plan_tokens / per_action_tokens, 1)
    return plan_capacity(plan_tokens, action)


# ── pricing_versions — margin / Stripe fee, effective-dated ────────────

async def get_effective_pricing_version(db) -> dict:
    """The most recent version with effective_from <= now. Falls back
    to the hardcoded DEFAULT_* constants (tagged `_id: "default"`) if
    no row has been set yet — this module must never raise just
    because an admin hasn't opened the margin editor (Part 3) yet."""
    now = time.time()
    row = await db.pricing_versions.find_one(
        {"effective_from": {"$lte": now}},
        sort=[("effective_from", -1), ("seq", -1)],
    )
    if row:
        return row
    return {
        "_id": "default", "margin": DEFAULT_MARGIN,
        "stripe_fee_pct": DEFAULT_STRIPE_FEE_PCT,
        "stripe_fee_fixed_usd": DEFAULT_STRIPE_FEE_FIXED_USD,
        "effective_from": 0, "actor": "default",
    }


def validate_margin_input(margin) -> float:
    """PART 3 strict validation for the admin-facing margin editor.
    Rejects anything that isn't a genuine finite number in
    [MARGIN_MIN, MARGIN_MAX] — explicitly including bool (Python's
    `isinstance(True, int)` is True, so `True` would otherwise sail
    through as 1.0), NaN, +/-Infinity, strings, None, lists/dicts
    (Mongo-operator-injection shaped or otherwise), and out-of-bounds
    values. Returns the validated value as a plain float."""
    if isinstance(margin, bool) or not isinstance(margin, (int, float)):
        # Deliberately ValueError, not TypeError (TRY004) — callers
        # (routers/admin_pricing.py, this module's own tests) catch
        # ONE exception type for every validation failure here,
        # type-wrong or in-range-wrong alike.
        raise ValueError(  # noqa: TRY004
            f"margin must be a finite number, got {type(margin).__name__}: {margin!r}"
        )
    if not math.isfinite(margin):
        raise ValueError(f"margin must be finite, got {margin!r}")
    if not (MARGIN_MIN <= margin <= MARGIN_MAX):
        raise ValueError(
            f"margin must be between {MARGIN_MIN:.0%} and {MARGIN_MAX:.0%}, "
            f"got {margin:.2%}"
        )
    return float(margin)


async def _next_sequence(db, name: str) -> int:
    """Atomic monotonic counter — used to give every pricing_versions
    row a strictly-increasing `seq` as a tiebreaker sort key, so two
    admins saving at the same moment (same millisecond `effective_from`)
    still resolve to an unambiguous, deterministic "most recent"
    version rather than an undefined sort order between two rows with
    an identical timestamp."""
    doc = await db.pricing_seq.find_one_and_update(
        {"_id": name}, {"$inc": {"seq": 1}}, upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


async def set_pricing_version(
    db, *, margin: float, stripe_fee_pct: float = DEFAULT_STRIPE_FEE_PCT,
    stripe_fee_fixed_usd: float = DEFAULT_STRIPE_FEE_FIXED_USD,
    effective_from: float | None = None, actor: str,
) -> dict:
    """Append a new pricing version — never mutates a prior version in
    place, so a run settled under an old margin stays auditable against
    the version that was actually in effect when it happened.

    This is the permissive, data-model-level bound ([0, 1) — used by
    internal callers, tests, and migrations). The admin-facing margin
    editor (routers/admin_pricing.py) enforces the TIGHTER
    [MARGIN_MIN, MARGIN_MAX] business rule via `validate_margin_input`
    before ever calling this."""
    if not (0 <= margin < 1):
        raise ValueError(f"margin must be in [0, 1), got {margin!r}")
    now = time.time()
    seq = await _next_sequence(db, "pricing_versions")
    doc = {
        "_id": f"pv_{int(now * 1000)}_{seq}",
        "seq": seq,
        "margin": margin,
        "stripe_fee_pct": stripe_fee_pct,
        "stripe_fee_fixed_usd": stripe_fee_fixed_usd,
        "effective_from": effective_from if effective_from is not None else now,
        "actor": actor,
        "created_at": now,
    }
    await db.pricing_versions.insert_one(doc)
    logger.info("[pricing_engine] new pricing version %s margin=%s actor=%s",
                doc["_id"], margin, actor)
    return doc


# ── rolling 7-day average actual cost per action (daily job) ───────────

async def compute_rolling_average_cost(db, action: str, *, days: int = 7) -> float | None:
    """Average real USD cost per settled/released run of `action` over
    the trailing `days`, from wallet_ledger's own settle (`actual_cost_usd`)
    and release (`failed_run_cost`) rows — the exact rows a real cost was
    ever recorded on. Returns None if there's no real-cost data yet (the
    common case until callers start passing `actual_cost_usd` into
    wallet.settle/release)."""
    from datetime import UTC, datetime, timedelta
    # `wallet_ledger.created_at` is a BSON Date (services.wallet._now())
    # — comparing it against a raw Unix-timestamp float never matches
    # (cross-type $gte on Date vs double is not what you'd guess from
    # BSON's type-sort order), so the cutoff must be a datetime too.
    cutoff = datetime.now(UTC) - timedelta(days=days)
    pipeline = [
        {"$match": {
            "action": action,
            "created_at": {"$gte": cutoff},
            "kind": {"$in": ["settle", "release"]},
        }},
        {"$project": {
            "cost": {"$ifNull": ["$actual_cost_usd", "$failed_run_cost"]},
        }},
        {"$match": {"cost": {"$ne": None, "$gt": 0}}},
        {"$group": {"_id": None, "avg_cost": {"$avg": "$cost"}, "samples": {"$sum": 1}}},
    ]
    rows = await db.wallet_ledger.aggregate(pipeline).to_list(1)
    if not rows:
        return None
    return rows[0]["avg_cost"]


async def run_rolling_cost_update(db, *, dry_run: bool = False) -> dict:
    """Daily job — recompute the rolling 7-day average actual cost for
    every priced action and store it in `action_cost_stats` (one row
    per action, `_id = action`) for the admin dashboard (Part 3) and
    `plan_capacity_live` to read. dry_run=True computes and returns
    without writing."""
    from config.token_pricing import PRICE_TABLE
    now = time.time()
    results = []
    for action in PRICE_TABLE:
        avg_cost = await compute_rolling_average_cost(db, action, days=7)
        results.append({"action": action, "avg_cost_usd_7d": avg_cost})
        if dry_run or avg_cost is None:
            continue
        await db.action_cost_stats.update_one(
            {"_id": action},
            {"$set": {"avg_cost_usd_7d": avg_cost, "updated_at": now}},
            upsert=True,
        )
    return {"dry_run": dry_run, "results": results}


# ── PART 3 — fixed monthly costs (admin panel) ──────────────────────────

async def list_fixed_costs(db) -> list[dict]:
    return await db.fixed_costs.find({}).sort("name", 1).to_list(1000)


async def set_fixed_cost(
    db, *, cost_id: str | None, name: str, amount_usd_monthly: float, actor: str,
) -> dict:
    """Create (`cost_id=None`) or update (`cost_id` given) a fixed
    monthly cost line (hosting, API fees, etc. — costs that aren't a
    per-action LLM spend). `name` is stored EXACTLY as submitted, raw —
    never HTML-escaped at write time (escaping here would corrupt the
    name for any non-HTML consumer, e.g. this same JSON API); escaping
    is the FRONTEND's job at render time (plain JSX text interpolation
    auto-escapes — see frontend/src/pages/AdminPricing.jsx)."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if (isinstance(amount_usd_monthly, bool)
            or not isinstance(amount_usd_monthly, (int, float))
            or not math.isfinite(amount_usd_monthly)
            or amount_usd_monthly < 0):
        raise ValueError(
            f"amount_usd_monthly must be a finite, non-negative number, "
            f"got {amount_usd_monthly!r}"
        )
    now = time.time()
    if cost_id:
        _require_str_pe("cost_id", cost_id)
        doc = await db.fixed_costs.find_one_and_update(
            {"_id": cost_id},
            {"$set": {"name": name, "amount_usd_monthly": float(amount_usd_monthly),
                       "updated_at": now, "updated_by": actor}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise ValueError(f"fixed cost {cost_id!r} not found")
        return doc
    doc = {
        "_id": f"fc_{_uuid.uuid4().hex[:12]}", "name": name,
        "amount_usd_monthly": float(amount_usd_monthly),
        "created_at": now, "created_by": actor,
        "updated_at": now, "updated_by": actor,
    }
    await db.fixed_costs.insert_one(doc)
    return doc


async def delete_fixed_cost(db, cost_id: str) -> bool:
    _require_str_pe("cost_id", cost_id)
    res = await db.fixed_costs.delete_one({"_id": cost_id})
    return res.deleted_count > 0


async def total_fixed_costs_monthly(db) -> float:
    rows = await db.fixed_costs.find({}, {"amount_usd_monthly": 1}).to_list(1000)
    return sum(r.get("amount_usd_monthly", 0) for r in rows)


def _require_str_pe(name: str, value) -> None:
    """Same Mongo-operator-injection guard as services.wallet._require_str
    (PRE-STEP (a) hardening), applied to this module's own id-shaped
    parameters."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}: {value!r}")


# ── PART 3 — immutable audit log ────────────────────────────────────────
#
# PRE-STEP (b) (Master Prompt v2 continuation, fixing a PART 3 review
# finding) — append-only is no longer just "nothing in this module
# calls .update/.delete": every row now also carries a hash chain
# (`prev_hash` + `row_hash`) so an edit or deletion made DIRECTLY
# against the `pricing_audit_log` collection (bypassing this module
# entirely) is detectable by verify_audit_chain, not merely absent
# from this module's own source.

_AUDIT_GENESIS_HASH = "genesis"


def _audit_row_hash(row: dict, prev_hash: str) -> str:
    """sha256 of `prev_hash` + this row's own content (every field
    except `row_hash` itself). Deterministic: two rows with identical
    content but a different `_id`/`created_at` still hash differently
    because `_id` and `created_at` are part of the content."""
    payload = json.dumps(
        {k: v for k, v in row.items() if k != "row_hash"},
        sort_keys=True, default=str,
    )
    return hashlib.sha256((prev_hash + payload).encode()).hexdigest()


async def write_audit_row(
    db, *, action: str, actor: str, before: dict | None, after: dict | None,
    detail: dict | None = None,
) -> dict:
    """Append-only audit row for ANY pricing-version or fixed-cost
    change. Nothing in this module ever updates or deletes a row from
    `pricing_audit_log` — it is the one place "what changed, who
    changed it, and what it was before" is guaranteed to still be
    readable regardless of how many margin changes happen later.

    Each row also chains onto the previous row's hash (see
    _audit_row_hash / verify_audit_chain) so a tamper made directly
    against Mongo — not through this function — is still detectable.
    The chain-head pointer (`pricing_audit_chain_head`, a single
    document) is advanced via an optimistic compare-and-swap retry
    loop — the same pattern services.wallet already uses for FIFO
    batch versions — so concurrent writers can never both read the
    same "previous hash" and fork the chain: only one CAS can win for
    a given `version`, and the loser retries against the new head.

    The row's chain position is recorded as `seq` (the post-increment
    CAS version), NOT inferred from `created_at` — under concurrency,
    `time.time()` is coarse enough that two rows can legitimately tie
    on wall-clock time while still having a strict, CAS-determined
    chain order; sorting by `created_at` in verify_audit_chain would
    then disagree with the order the hashes were actually chained in
    and falsely report tampering. `seq` is the only ordering key that
    is guaranteed to match the CAS-established chain order."""
    now = time.time()
    for _ in range(25):
        head = await db.pricing_audit_chain_head.find_one({"_id": "head"})
        version = (head or {}).get("version", 0)
        prev_hash = (head or {}).get("last_hash", _AUDIT_GENESIS_HASH)
        seq = version + 1

        row = {
            "_id": f"audit_{_uuid.uuid4().hex[:16]}",
            "action": action, "actor": actor,
            "before": before, "after": after, "detail": detail or {},
            "created_at": now,
            "seq": seq,
            "prev_hash": prev_hash,
        }
        row["row_hash"] = _audit_row_hash(row, prev_hash)

        if head is None:
            res = await db.pricing_audit_chain_head.update_one(
                {"_id": "head"},
                {"$setOnInsert": {"last_hash": row["row_hash"], "version": seq}},
                upsert=True,
            )
            won = res.upserted_id is not None
        else:
            res = await db.pricing_audit_chain_head.update_one(
                {"_id": "head", "version": version},
                {"$set": {"last_hash": row["row_hash"], "version": seq}},
            )
            won = res.modified_count == 1

        if won:
            await db.pricing_audit_log.insert_one(row)
            return row
        # Lost the CAS race to another concurrent writer — retry with
        # whatever the head looks like now.
    raise RuntimeError("write_audit_row: exhausted retries claiming a chain position")


async def list_audit_log(db, *, limit: int = 200) -> list[dict]:
    return await db.pricing_audit_log.find({}).sort("created_at", -1).to_list(limit)


async def verify_audit_chain(db) -> dict:
    """Walks `pricing_audit_log` in chain order (`seq` asc — NOT
    `created_at`, which can tie under concurrent writers; see
    write_audit_row's docstring) and recomputes each row's hash from
    its own content plus the PRECEDING row's stored `row_hash`,
    comparing against what's actually stored. Returns {"ok",
    "broken_at", "checked"}.

    Detects both an EDITED row (its own recomputed hash no longer
    matches its stored row_hash) and a DELETED row (the next
    surviving row's `prev_hash` no longer matches the hash of
    whatever row now immediately precedes it, since that predecessor
    changed)."""
    rows = await db.pricing_audit_log.find({}).sort("seq", 1).to_list(1_000_000)
    prev_hash = _AUDIT_GENESIS_HASH
    for row in rows:
        expected = _audit_row_hash(row, prev_hash)
        if row.get("row_hash") != expected:
            return {"ok": False, "broken_at": row["_id"], "checked": len(rows)}
        prev_hash = row["row_hash"]
    return {"ok": True, "broken_at": None, "checked": len(rows)}


# ── PART 3 — margin-editor preview/confirm token ────────────────────────
#
# Stateless, signed, short-lived preview that CONFIRM must echo back
# verbatim: binds what was shown in the preview to what confirm
# actually applies (a client can't preview 20% and silently confirm
# 79%), expires after _PREVIEW_TTL_SECONDS, and is single-use (checked
# against `pricing_preview_used` at confirm time via the same
# insert-or-DuplicateKeyError idempotency pattern the wallet uses) —
# a captured preview+confirm request pair replayed later is rejected
# as an already-used token. Bearer-token APIs like this one aren't
# vulnerable to classic cookie-based CSRF (no ambient credential a
# cross-site form could attach), so this single-use binding is this
# endpoint's actual replay defense, not a separate CSRF-token scheme.

def _preview_secret() -> bytes:
    from cto_services.auth import JWT_SECRET
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET must be set: refusing to sign preview tokens without a configured secret")
    return JWT_SECRET.encode()


def make_preview_token(payload: dict) -> dict:
    expires_at = time.time() + _PREVIEW_TTL_SECONDS
    body = {**payload, "expires_at": expires_at}
    raw = json.dumps(body, sort_keys=True, default=str)
    sig = _hmac.new(_preview_secret(), raw.encode(), hashlib.sha256).hexdigest()
    return {**body, "token": sig}


async def verify_and_consume_preview_token(db, payload: dict, token: str) -> None:
    """Raises ValueError if the token doesn't match `payload`, has
    expired, or has already been consumed (replay). Never raises for
    any other reason — a bug here must reject, not silently pass."""
    _require_str_pe("token", token)
    expires_at = payload.get("expires_at")
    if not expires_at or time.time() > float(expires_at):
        raise ValueError("preview token expired — request a new preview")
    raw = json.dumps(payload, sort_keys=True, default=str)
    expected = _hmac.new(_preview_secret(), raw.encode(), hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(expected, token):
        raise ValueError("preview token does not match the submitted values")
    try:
        await db.pricing_preview_used.insert_one({"_id": token, "used_at": time.time()})
    except DuplicateKeyError:
        raise ValueError("preview token already used — request a new preview")


# ── PART 3 — admin dashboard aggregation ────────────────────────────────

async def compute_dashboard(db) -> dict:
    """Read-only. Never writes anything. Every number here is computed
    fresh from wallet_ledger/wallets/billing_flags/real_llm_usage — no
    cached/denormalized state to go stale."""
    from config.token_pricing import tokens_to_usd

    # PRE-STEP (a) continuation (Master Prompt v2) — revenue MUST sum
    # `amount_micro` (exact, 1 token = 1000 micro-tokens), not the
    # legacy `amount` field. settle() now FLOORS `amount` to whole
    # tokens (e.g. a genuine 62-micro-token charge floors to `amount=0`)
    # — summing the legacy field here would silently under-report
    # revenue for every fractional (sub-1-token) charge, the exact
    # same class of leak this whole fix exists to close. `$ifNull`
    # falls back to `amount*1000` for any row that predates this field
    # (none exist in production — TOKEN_BILLING_MODE has never been
    # "on" — but defensive regardless).
    by_action_pipeline = [
        {"$match": {"kind": "settle", "actual_cost_usd": {"$ne": None}}},
        {"$group": {
            "_id": "$action",
            "revenue_usd": {"$sum": {"$divide": [
                {"$ifNull": ["$amount_micro", {"$multiply": ["$amount", 1000]}]},
                100_000,
            ]}},
            "real_cost_usd": {"$sum": "$actual_cost_usd"},
            "runs": {"$sum": 1},
        }},
    ]
    by_action = []
    async for row in db.wallet_ledger.aggregate(by_action_pipeline):
        revenue = row["revenue_usd"]
        cost = row["real_cost_usd"]
        by_action.append({
            "action": row["_id"], "revenue_usd": round(revenue, 4),
            "real_cost_usd": round(cost, 4), "runs": row["runs"],
            "realized_margin": round((revenue - cost) / revenue, 4) if revenue > 0 else None,
        })

    by_user_pipeline = [
        {"$match": {"kind": "settle", "actual_cost_usd": {"$ne": None}}},
        {"$group": {
            "_id": "$user_id",
            "revenue_usd": {"$sum": {"$divide": [
                {"$ifNull": ["$amount_micro", {"$multiply": ["$amount", 1000]}]},
                100_000,
            ]}},
            "real_cost_usd": {"$sum": "$actual_cost_usd"},
        }},
        {"$sort": {"revenue_usd": -1}},
        {"$limit": 50},
    ]
    by_user = []
    async for row in db.wallet_ledger.aggregate(by_user_pipeline):
        revenue = row["revenue_usd"]
        cost = row["real_cost_usd"]
        by_user.append({
            # user_id only — never email/other PII (Part 3 security rule).
            "user_id": row["_id"], "revenue_usd": round(revenue, 4),
            "real_cost_usd": round(cost, 4),
            "realized_margin": round((revenue - cost) / revenue, 4) if revenue > 0 else None,
        })

    ceiling_pipeline = [
        {"$match": {"kind": "settle", "computed_tokens_uncapped": {"$ne": None}}},
        {"$project": {
            "absorbed": {"$max": [0, {"$subtract": ["$computed_tokens_uncapped", "$held_amount"]}]},
            "hit": {"$gt": ["$computed_tokens_uncapped", "$held_amount"]},
        }},
        {"$match": {"hit": True}},
        {"$group": {"_id": None, "count": {"$sum": 1}, "absorbed_tokens": {"$sum": "$absorbed"}}},
    ]
    ceiling_rows = await db.wallet_ledger.aggregate(ceiling_pipeline).to_list(1)
    ceiling_hit = ceiling_rows[0] if ceiling_rows else {"count": 0, "absorbed_tokens": 0}

    failed_pipeline = [
        {"$match": {"kind": "release", "failed_run_cost": {"$ne": None, "$gt": 0}}},
        {"$group": {"_id": None, "total_usd": {"$sum": "$failed_run_cost"}, "count": {"$sum": 1}}},
    ]
    failed_rows = await db.wallet_ledger.aggregate(failed_pipeline).to_list(1)
    failed_run_cost = failed_rows[0] if failed_rows else {"total_usd": 0, "count": 0}

    owed_pipeline = [
        {"$group": {"_id": None, "total_tokens": {"$sum": {"$add": [
            {"$ifNull": ["$buckets.promo", 0]},
            {"$ifNull": ["$buckets.plan", 0]},
            {"$ifNull": ["$buckets.purchased", 0]},
        ]}}}},
    ]
    owed_rows = await db.wallets.aggregate(owed_pipeline).to_list(1)
    tokens_owed = owed_rows[0]["total_tokens"] if owed_rows else 0

    fixed_costs_monthly = await total_fixed_costs_monthly(db)

    # Unmetered-cost alarm — real LLM spend (real_llm_usage, the
    # platform-wide canonical ledger) vs what's actually flowing
    # through wallet.settle/release's cost-tracking fields. A large
    # gap means real spend is happening on a path that never reports
    # actual_cost_usd/failed_run_cost at all.
    now_ts = time.time()
    real_usage_rows = await db.real_llm_usage.aggregate([
        {"$match": {"ts": {"$gte": now_ts - 7 * 86400}, "status": "ok"}},
        {"$group": {"_id": None, "total_usd": {"$sum": "$cost_usd"}}},
    ]).to_list(1)
    real_llm_cost_7d = real_usage_rows[0]["total_usd"] if real_usage_rows else 0.0

    from datetime import UTC, datetime, timedelta
    wallet_cutoff = datetime.now(UTC) - timedelta(days=7)
    wallet_tracked_rows = await db.wallet_ledger.aggregate([
        {"$match": {"created_at": {"$gte": wallet_cutoff}, "kind": {"$in": ["settle", "release"]}}},
        {"$project": {"cost": {"$ifNull": ["$actual_cost_usd", "$failed_run_cost"]}}},
        {"$match": {"cost": {"$ne": None}}},
        {"$group": {"_id": None, "total_usd": {"$sum": "$cost"}}},
    ]).to_list(1)
    wallet_tracked_cost_7d = wallet_tracked_rows[0]["total_usd"] if wallet_tracked_rows else 0.0

    unmetered_gap_usd = round(real_llm_cost_7d - wallet_tracked_cost_7d, 4)
    unmetered_alarm = unmetered_gap_usd > max(5.0, 0.2 * max(real_llm_cost_7d, 0.01))

    version = await get_effective_pricing_version(db)

    # PRE-STEP (e) (Master Prompt v2 continuation) — reconcile_report()
    # itself returns `rows`/`flagged`: EVERY user compared this month
    # (uncapped, unlike by_user_top50's deliberate 50-row limit below),
    # each with their own token-consumption numbers. Nothing on the
    # frontend (AdminPricingPanel.jsx) reads those two lists — only
    # `users_compared`/`flagged_count` — so this dashboard embeds just
    # the three aggregate fields actually consumed. reconcile_report()
    # itself is untouched: other, non-HTTP callers (the reconciliation
    # job / its own test) still get the full per-user breakdown.
    from services.wallet_reconciliation import reconcile_report
    _drift_full = await reconcile_report(db)
    drift = {
        "month": _drift_full["month"],
        "users_compared": _drift_full["users_compared"],
        "flagged_count": _drift_full["flagged_count"],
    }

    flags_summary: dict[str, dict[str, int]] = {}
    async for row in db.billing_flags.aggregate([
        {"$group": {"_id": {"flag_type": "$flag_type", "resolved": "$resolved"}, "count": {"$sum": 1}}},
    ]):
        ftype = row["_id"]["flag_type"]
        resolved = bool(row["_id"]["resolved"])
        flags_summary.setdefault(ftype, {"open": 0, "resolved": 0})
        flags_summary[ftype]["resolved" if resolved else "open"] += row["count"]

    total_revenue = sum(r["revenue_usd"] for r in by_action)
    break_even_progress_pct = (
        round(100 * total_revenue / fixed_costs_monthly, 1) if fixed_costs_monthly > 0 else None
    )

    return {
        "current_margin": version["margin"],
        "current_pricing_version_id": version["_id"],
        "by_action": by_action,
        "by_user_top50": by_user,
        "ceiling_hit": {
            "count": ceiling_hit.get("count", 0),
            "absorbed_tokens": ceiling_hit.get("absorbed_tokens", 0),
            "absorbed_usd": tokens_to_usd(ceiling_hit.get("absorbed_tokens", 0)),
        },
        "failed_run_cost": {
            "total_usd": round(failed_run_cost.get("total_usd", 0) or 0, 4),
            "count": failed_run_cost.get("count", 0),
        },
        "tokens_owed": tokens_owed,
        "tokens_owed_usd": tokens_to_usd(tokens_owed),
        "fixed_costs_monthly_usd": round(fixed_costs_monthly, 2),
        "total_revenue_usd": round(total_revenue, 4),
        "break_even_progress_pct": break_even_progress_pct,
        "reconcile_drift": drift,
        "unmetered_cost_alarm": {
            "alarm": unmetered_alarm,
            "real_llm_cost_7d_usd": round(real_llm_cost_7d, 4),
            "wallet_tracked_cost_7d_usd": round(wallet_tracked_cost_7d, 4),
            "gap_usd": unmetered_gap_usd,
        },
        "billing_flags_summary": flags_summary,
    }
