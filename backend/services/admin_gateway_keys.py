"""
services/admin_gateway_keys.py — 2026-09-28 ORA Chat MCP/Gateway keys.

Shared key-management for BOTH new admin-only access paths:
  - scope="mcp"     → the ask_ora_chat MCP server (routers/mcp_ora_chat.py)
  - scope="gateway" → the Anthropic-compatible Claude Desktop gateway
                       (Part B, built separately)

Security model (per founder's explicit spec):
  - Keys are shown to the admin ONCE at creation. We store ONLY a
    SHA-256 hash — the raw value is never persisted, logged, or
    returned again.
  - Every verify() call re-checks the OWNING USER is still an admin
    right now (live DB read), not just a flag frozen at key-creation
    time — a demoted admin's old keys stop working immediately.
  - Per-key rate limit (requests/min) and a hard monthly spend cap in
    USD, both admin-settable and enforced server-side on every call.
  - Usage log never stores message content — only timing, the
    tool/model name, token counts, cost, and status.

Two Mongo collections:
  - `admin_gateway_keys`      — one doc per key (hash, limits, scope)
  - `admin_gateway_usage_log` — one doc per call (no message bodies)
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from cto_services.db import get_db

DEFAULT_RATE_LIMIT_PER_MIN = 60
DEFAULT_SPEND_CAP_USD = 50.0
ALERT_THRESHOLD_PCT = 0.80

_KEY_PREFIXES = {"mcp": "oraadm_mcp_", "gateway": "oraadm_gw_"}


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _month_key(ts: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


async def generate_key(scope: str, label: str, created_by_user_id: str,
                        rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN,
                        spend_cap_monthly_usd: float = DEFAULT_SPEND_CAP_USD) -> tuple[str, dict]:
    """Create a new key. Returns (raw_key, doc) — raw_key is ONLY ever
    available here, at creation time. Caller must show it once and
    never log/persist it elsewhere."""
    if scope not in _KEY_PREFIXES:
        raise ValueError(f"Unknown scope: {scope!r}")
    db = get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    raw = _KEY_PREFIXES[scope] + secrets.token_urlsafe(32)
    now = time.time()
    doc = {
        "key_id": secrets.token_hex(8),
        "key_hash": _hash_key(raw),
        "key_prefix": raw[:len(_KEY_PREFIXES[scope]) + 6],  # e.g. "oraadm_mcp_AbCdEf" for display
        "scope": scope,
        "label": (label or "").strip()[:80] or "Unnamed key",
        "created_by_user_id": created_by_user_id,
        "created_at": now,
        "last_used_at": None,
        "revoked": False,
        "revoked_at": None,
        "rate_limit_per_min": int(rate_limit_per_min),
        "spend_cap_monthly_usd": float(spend_cap_monthly_usd),
    }
    await db.admin_gateway_keys.insert_one(doc)
    doc.pop("_id", None)
    return raw, doc


_last_used_write: dict[str, float] = {}
_TOUCH_THROTTLE_S = 20.0


async def verify_key(raw_key: Optional[str], scope: str) -> Optional[dict]:
    """Look up a key by hash, confirm scope + not revoked + owning
    user is STILL an admin right now. Returns the key doc (never the
    raw value) or None on any failure."""
    if not raw_key or not raw_key.startswith(_KEY_PREFIXES.get(scope, "\x00")):
        return None
    db = get_db()
    if db is None:
        return None
    key_hash = _hash_key(raw_key)
    doc = await db.admin_gateway_keys.find_one(
        {"key_hash": key_hash, "scope": scope, "revoked": {"$ne": True}},
    )
    if not doc:
        return None
    owner = await db.dev_users.find_one(
        {"user_id": doc["created_by_user_id"]},
        {"is_admin": 1, "tier": 1},
    )
    if not owner or not (owner.get("is_admin") or owner.get("tier") == "founder"):
        return None
    # Throttled last_used_at touch — same pattern as presence tracking,
    # avoids a write on every single tool call.
    now = time.monotonic()
    last = _last_used_write.get(doc["key_id"])
    if last is None or (now - last) >= _TOUCH_THROTTLE_S:
        _last_used_write[doc["key_id"]] = now
        await db.admin_gateway_keys.update_one(
            {"key_id": doc["key_id"]}, {"$set": {"last_used_at": time.time()}},
        )
    doc.pop("_id", None)
    return doc


async def current_month_spend_usd(key_id: str) -> float:
    db = get_db()
    if db is None:
        return 0.0
    month = _month_key()
    rows = db.admin_gateway_usage_log.aggregate([
        {"$match": {"key_id": key_id, "month": month}},
        {"$group": {"_id": None, "total": {"$sum": "$cost_usd"}}},
    ])
    async for r in rows:
        return round(float(r.get("total") or 0.0), 6)
    return 0.0


async def check_spend_cap(key_doc: dict) -> tuple[bool, float]:
    """Returns (allowed, current_month_spend_usd)."""
    spent = await current_month_spend_usd(key_doc["key_id"])
    cap = float(key_doc.get("spend_cap_monthly_usd") or DEFAULT_SPEND_CAP_USD)
    return spent < cap, spent


async def log_usage(key_id: str, tool_or_model: str, tokens_in: int = 0,
                     tokens_out: int = 0, cost_usd: float = 0.0,
                     status: str = "ok") -> None:
    """Log ONE call. Never pass message content in here — this log is
    read by the admin panel usage table and must stay content-free."""
    db = get_db()
    if db is None:
        return
    now = time.time()
    await db.admin_gateway_usage_log.insert_one({
        "key_id": key_id,
        "ts": now,
        "month": _month_key(now),
        "tool_or_model": tool_or_model,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "cost_usd": round(float(cost_usd or 0.0), 6),
        "status": status,
    })


async def list_keys(scope: str) -> list[dict]:
    db = get_db()
    if db is None:
        return []
    out = []
    cursor = db.admin_gateway_keys.find(
        {"scope": scope}, {"_id": 0, "key_hash": 0},
    ).sort("created_at", -1)
    async for doc in cursor:
        spent = await current_month_spend_usd(doc["key_id"])
        cap = float(doc.get("spend_cap_monthly_usd") or DEFAULT_SPEND_CAP_USD)
        pct = (spent / cap) if cap > 0 else 0.0
        doc["current_month_spend_usd"] = spent
        doc["spend_pct"] = round(pct, 4)
        doc["over_alert_threshold"] = pct >= ALERT_THRESHOLD_PCT
        out.append(doc)
    return out


async def revoke_key(key_id: str, scope: str) -> bool:
    db = get_db()
    if db is None:
        return False
    r = await db.admin_gateway_keys.update_one(
        {"key_id": key_id, "scope": scope},
        {"$set": {"revoked": True, "revoked_at": time.time()}},
    )
    return r.matched_count > 0


async def update_limits(key_id: str, scope: str, rate_limit_per_min: Optional[int] = None,
                         spend_cap_monthly_usd: Optional[float] = None) -> bool:
    db = get_db()
    if db is None:
        return False
    updates: dict = {}
    if rate_limit_per_min is not None:
        updates["rate_limit_per_min"] = int(rate_limit_per_min)
    if spend_cap_monthly_usd is not None:
        updates["spend_cap_monthly_usd"] = float(spend_cap_monthly_usd)
    if not updates:
        return False
    r = await db.admin_gateway_keys.update_one(
        {"key_id": key_id, "scope": scope}, {"$set": updates},
    )
    return r.matched_count > 0
