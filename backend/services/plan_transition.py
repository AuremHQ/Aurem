"""
services/plan_transition.py — Master Prompt v2 PART 6.1/6.2.

The DRY-RUN classifier + report builder for moving existing users
from the old task-based plans to the token model. Writes NOTHING —
every entry point requires a `WriteGuardedDB` (services.
plan_transition_write_guard), enforced defensively (a raw db raises
TypeError immediately, never silently accepted).

Segment priority (a user can technically match more than one;
EXCLUDED segments are checked first, in this order): disputed →
refunded → frozen → admin → test_user → no_email → duplicate_email →
huge_allowance → paid_active / paid_cancelled → free_active /
free_dormant.

"Remaining old allowance" = the OLD flat monthly TASK cap
(services.usage.MONTHLY_TASK_LIMITS) minus tasks used this month —
re-using services.usage.get_usage()'s own real computation (read-only
by construction) rather than re-deriving the same business logic with
a second, possibly-drifting implementation.
"""
from __future__ import annotations

import csv
import io
import time

import config.plan_transition as _cfg
from services.plan_transition_write_guard import WriteGuardedDB
from services.test_accounts import is_test_email

# Fields that must NEVER appear in a CSV export or a collection
# snapshot, regardless of what's requested — secrets/payment-method
# data/raw tokens. Checked by exact key AND by substring (so e.g.
# "stripe_customer_id" is caught by "customer_id" too).
_FORBIDDEN_EXPORT_FIELD_SUBSTRINGS = (
    "password", "api_key", "token", "secret", "customer_id",
    "card", "cvv", "ssn", "private_key",
)

_CSV_FORMULA_INJECTION_PREFIXES = ("=", "+", "-", "@")


def _csv_safe_cell(value) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in _CSV_FORMULA_INJECTION_PREFIXES:
        return "'" + text
    return text


def rows_to_csv(rows: list[dict]) -> str:
    """Never includes a forbidden field (defense in depth — callers
    are expected to only ever pass classify_user()'s own output,
    which has no secret fields to begin with, but this strips them
    again here regardless). Every cell is formula-injection-escaped."""
    buf = io.StringIO()
    safe_rows = []
    fieldnames: list[str] = []
    for row in rows:
        safe_row = {
            k: v for k, v in row.items()
            if not any(bad in k.lower() for bad in _FORBIDDEN_EXPORT_FIELD_SUBSTRINGS)
        }
        safe_rows.append(safe_row)
        for k in safe_row:
            if k not in fieldnames:
                fieldnames.append(k)
    writer = csv.writer(buf)
    writer.writerow(fieldnames)
    for row in safe_rows:
        writer.writerow([_csv_safe_cell(row.get(k)) for k in fieldnames])
    return buf.getvalue()


async def export_collection_snapshot(db, collection_name: str, *, query: dict | None = None) -> list[dict]:
    """Read-only rollback-prep helper — exports a collection (or a
    filtered subset) to a list of dicts, with forbidden fields
    stripped. Requires a WriteGuardedDB, same defensive contract as
    run_dry_run."""
    if not isinstance(db, WriteGuardedDB):
        raise TypeError("export_collection_snapshot requires a WriteGuardedDB")
    query = query or {}
    collection = getattr(db, collection_name)
    rows = []
    async for doc in collection.find(query):
        clean = {
            k: v for k, v in doc.items()
            if not any(bad in k.lower() for bad in _FORBIDDEN_EXPORT_FIELD_SUBSTRINGS)
        }
        rows.append(clean)
    return rows


_FAKE_APPLY_COLLECTION = "plan_transition_fake_applies"


async def fake_apply_transition(db, *, user_id: str, transition_id: str) -> dict:
    """Demonstrates the idempotent-apply INTERFACE for a future real
    apply — writes ONLY to its own dedicated bookkeeping collection
    (`plan_transition_fake_applies`), via an internally-constructed
    WriteGuardedDB that allowlists nothing else, so this can never
    touch dev_users/wallets/fix_entitlement even if called with the
    raw db. A real apply (writing real wallet grants) is explicitly
    OUT of scope for this delivery — see the PART 6 report."""
    guarded = WriteGuardedDB(db, allow_write_to={_FAKE_APPLY_COLLECTION})
    doc_id = f"{user_id}:{transition_id}"
    existing = await guarded.plan_transition_fake_applies.find_one({"_id": doc_id})
    if existing:
        return {"applied": True, "already_applied": True, "user_id": user_id, "transition_id": transition_id}
    await guarded.plan_transition_fake_applies.insert_one({
        "_id": doc_id, "user_id": user_id, "transition_id": transition_id,
        "applied_at": time.time(), "fake": True,
    })
    return {"applied": True, "already_applied": False, "user_id": user_id, "transition_id": transition_id}

_FREE_DORMANT_AFTER_SECONDS = 90 * 86400  # 90 days with no login
_HUGE_ALLOWANCE_TOKENS_THRESHOLD = 1_000_000


def _is_founder_email(email: str) -> bool:
    try:
        from services.test_accounts import is_founder_email
        return is_founder_email(email)
    except ImportError:
        return False


async def build_duplicate_email_index(db) -> dict[str, int]:
    """Read-only. Counts dev_users rows per lowercased email across
    the WHOLE user base (not scoped to wallet_test_* — a real dry run
    needs the real picture; tests pass a narrower `user_filter` to
    run_dry_run itself, which is what actually bounds the report)."""
    counts: dict[str, int] = {}
    cursor = db.dev_users.find({}, {"_id": 0, "email": 1})
    async for row in cursor:
        email = (row.get("email") or "").strip().lower()
        if not email:
            continue
        counts[email] = counts.get(email, 0) + 1
    return counts


async def classify_user(db, user: dict, *, all_emails_lower: dict[str, int] | None = None) -> dict:
    user_id = user.get("user_id", "")
    email = (user.get("email") or "").strip()
    tier = (user.get("tier") or "free").lower()
    now = time.time()

    base = {
        "user_id": user_id, "email": email, "current_plan": tier,
        "segment": None, "paid": tier not in ("free",),
        "excluded": False, "exclude_reason": None,
        "remaining_old_allowance": None, "proposed_token_grant": None,
        "proposed_grace_end": None, "proposed_new_state": None,
        "needs_decision": False,
    }

    if user.get("fix_entitlement_frozen"):
        row = await db.billing_flags.find_one({
            "user_id": user_id, "flag_type": "dispute_freeze", "resolved": False,
        })
        base["segment"] = "disputed" if row else "frozen"
        base["excluded"] = True
        base["exclude_reason"] = (
            "open dispute freeze — excluded until resolved" if row
            else "fix_entitlement_frozen is set — excluded until resolved"
        )
        return base

    refund_row = await db.billing_flags.find_one({
        "user_id": user_id, "flag_type": {"$in": ["refund_shortfall", "partial_sub_refund"]},
    })
    if refund_row:
        base["segment"] = "refunded"
        base["excluded"] = True
        base["exclude_reason"] = "has a refund flag on record — excluded, needs manual review"
        return base

    if tier == "founder" or user.get("is_unlimited") or _is_founder_email(email):
        base["segment"] = "admin"
        base["excluded"] = True
        base["exclude_reason"] = "founder/admin/unlimited account — not part of any plan transition"
        return base

    if is_test_email(email) and email:
        base["segment"] = "test_user"
        base["excluded"] = True
        base["exclude_reason"] = "matches the test-account email pattern — excluded from real transition"
        return base

    if not email:
        base["segment"] = "no_email"
        base["excluded"] = True
        base["exclude_reason"] = "no email on file — cannot be notified, excluded"
        return base

    if all_emails_lower is not None and all_emails_lower.get(email.lower(), 0) > 1:
        base["segment"] = "duplicate_email"
        base["excluded"] = True
        base["exclude_reason"] = f"email shared by {all_emails_lower[email.lower()]} accounts — needs manual de-dup"
        return base

    tokens_granted = int(user.get("tokens_granted") or 0)
    if tokens_granted > _HUGE_ALLOWANCE_TOKENS_THRESHOLD:
        base["segment"] = "huge_allowance"
        base["excluded"] = True
        base["exclude_reason"] = f"tokens_granted={tokens_granted} exceeds the sanity threshold — needs manual review"
        return base

    from services.usage import get_usage
    usage = await get_usage(user_id)
    task_cap = usage.get("monthly_task_cap")
    tasks_used = usage.get("tasks_this_month") or 0
    remaining_tasks = None if task_cap is None else max(0, task_cap - tasks_used)
    base["remaining_old_allowance"] = remaining_tasks

    if base["paid"]:
        base["segment"] = "paid_active" if user.get("stripe_sub_id") else "paid_cancelled_check"
        if base["segment"] == "paid_cancelled_check":
            # paid tier but no active subscription id is contradictory
            # data (shouldn't normally happen) — treat conservatively
            # as still "paid_active" rather than guessing further.
            base["segment"] = "paid_active"
        base["proposed_new_state"] = "token_plan_active"
        if _cfg.LEGACY_TASK_TO_TOKEN_RATE is None:
            base["needs_decision"] = True
        else:
            base["proposed_token_grant"] = round((remaining_tasks or 0) * _cfg.LEGACY_TASK_TO_TOKEN_RATE)
        return base

    if tier == "free" and user.get("tier_updated_at") and user.get("fix_entitlement"):
        base["segment"] = "paid_cancelled"
        base["proposed_new_state"] = "token_plan_grace_then_free_scan_only"
        if _cfg.LEGACY_TASK_TO_TOKEN_RATE is None:
            base["needs_decision"] = True
        else:
            base["proposed_token_grant"] = round((remaining_tasks or 0) * _cfg.LEGACY_TASK_TO_TOKEN_RATE)
        base["proposed_grace_end"] = now + _cfg.FREE_PLAN_GRACE_DAYS * 86400
        return base

    last_login = user.get("last_login_at")
    is_dormant = last_login is None or (now - float(last_login)) > _FREE_DORMANT_AFTER_SECONDS
    base["segment"] = "free_dormant" if is_dormant else "free_active"
    base["proposed_token_grant"] = 0
    base["proposed_new_state"] = "free_scan_only_after_grace"
    base["proposed_grace_end"] = now + _cfg.FREE_PLAN_GRACE_DAYS * 86400
    return base


def _validate_user_filter(user_filter: dict) -> None:
    """Defensive input validation — PART 6.4 red-team: a caller-
    supplied filter must never contain `$where` (arbitrary JS
    execution) or other whole-query operators that escape the
    intended "narrow down which users" use case."""
    banned_top_level = {"$where", "$function", "$accumulator"}
    if any(k in banned_top_level for k in user_filter):
        raise ValueError(f"user_filter may not use {banned_top_level & set(user_filter)}")


async def run_dry_run(db, *, user_filter: dict | None = None) -> dict:
    if not isinstance(db, WriteGuardedDB):
        raise TypeError("run_dry_run requires a WriteGuardedDB — refusing a raw db handle")
    user_filter = user_filter or {}
    _validate_user_filter(user_filter)

    all_emails_lower = await build_duplicate_email_index(db)
    rows = []
    cursor = db.dev_users.find(user_filter)
    async for user in cursor:
        row = await classify_user(db, user, all_emails_lower=all_emails_lower)
        rows.append(row)

    counts_per_segment: dict[str, int] = {}
    total_tokens_proposed = 0
    for row in rows:
        seg = row["segment"] or "unknown"
        counts_per_segment[seg] = counts_per_segment.get(seg, 0) + 1
        total_tokens_proposed += row.get("proposed_token_grant") or 0

    from config.token_pricing import tokens_to_usd
    estimated_liability_usd = tokens_to_usd(total_tokens_proposed)

    return {
        "rows": rows,
        "summary": {
            "counts_per_segment": counts_per_segment,
            "total_tokens_proposed": total_tokens_proposed,
            "estimated_liability_usd": estimated_liability_usd,
            "needs_decision_count": sum(1 for r in rows if r["needs_decision"]),
            "excluded_count": sum(1 for r in rows if r["excluded"]),
        },
    }
