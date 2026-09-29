"""
services/admin_audit_log.py — 2026-09-28 MASTER TASK Part B (Batch 3)

Single write-side helper for the `db.admin_audit` collection so every
security/administrative event (login, failed auth, plan change, token
grant, admin action, feature-flag change) lands in ONE place with the
SAME shape, instead of each call site hand-rolling its own dict. Reuses
the collection `routers/admin_bin.py::admin_audit_actions` (GET
/admin/audit/actions) already reads from — `tier_change` / bulk-revoke
/ flag-idle rows written before this module existed already match this
shape byte-for-byte (ts/actor/action/*), so no migration is needed.

Schema (one document per event)::

    {
      "ts":     datetime (UTC, tz-aware),
      "actor":  str   — email/user_id of whoever triggered it,
                        "system" for unauthenticated events (a login
                        attempt for an email that doesn't exist yet),
      "action": str   — "login" | "feature_flag_change" |
                        "tier_change" | "token_grant" | ... (free-form,
                        existing call sites keep their own labels),
      "target": str | None — the user/resource the action was done TO,
      "result": str   — "success" | "failed" | "ok" | ...,
      "extra":  dict | None — action-specific detail.
    }

Best-effort: never raises, never blocks the caller's real request.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

COLLECTION = "admin_audit"


async def record_event(
    db,
    *,
    actor: str,
    action: str,
    target: Optional[str] = None,
    result: str = "ok",
    extra: Optional[dict[str, Any]] = None,
) -> None:
    if db is None:
        return
    try:
        await db[COLLECTION].insert_one({
            "ts":     datetime.now(timezone.utc),
            "actor":  actor or "system",
            "action": action,
            "target": target,
            "result": result,
            **({"extra": extra} if extra else {}),
        })
    except Exception as e:  # noqa: BLE001 — audit logging must never break the caller
        logger.warning("admin_audit_log.record_event failed (action=%s): %r", action, e)
