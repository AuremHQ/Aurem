"""Wallet reconciliation report (read-only), moved from services/wallet.py
so the pricing engine can import it without a two-way import cycle."""
from datetime import UTC, datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _year_month(dt: datetime | None = None) -> str:
    dt = dt or _now()
    return f"{dt.year:04d}-{dt.month:02d}"


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
