"""
services/fix_items_store.py — persistence for the fix_items layer
(additive over `cto_open_findings`), funnel events, checkup records and
flag registration.  The pure model lives in `fix_items_model`; callers
import everything through the `services.fix_items` facade.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from services.fix_items_model import (
    CHECKUP_FLAGS, STATUSES, _now, can_transition, normalize_severity, normalize_status,
    plain_copy, score_for, stage_for, stage_summary, to_fix_item,
)

logger = logging.getLogger(__name__)

# ── Persistence (additive over cto_open_findings) ─────────────────────
def _item_identity(item: dict) -> tuple[str, str, int, str]:
    """(scanner, rule_id, line, finding_id) with the backlog's id shape so
    rows written by older code and by the checkup dedupe."""
    ev = item.get("evidence") or {}
    scanner = item.get("scanner") or "checkup"
    rule_id = item.get("rule_id") or "unknown"
    file_ = ev.get("file") or item.get("file") or ""
    line_ = int(ev.get("line") or item.get("line") or 0)
    return scanner, rule_id, line_, item.get("id") or f"{scanner}::{file_}:{line_}:{rule_id}"


def _item_fields(item: dict, status: str, now: datetime, source: str) -> dict:
    scanner, rule_id, line_, _ = _item_identity(item)
    ev = item.get("evidence") or {}
    stage = item.get("stage") or stage_for(scanner, rule_id)
    title_plain, impact_plain = plain_copy(scanner, rule_id)
    return {
        "scanner": scanner,
        "rule_id": rule_id,
        "severity": normalize_severity(item.get("severity")),
        "file": ev.get("file") or item.get("file") or "",
        "line": line_,
        "category": item.get("category") or stage,
        "stage": stage,
        "title_plain": item.get("title_plain") or title_plain,
        "impact_plain": item.get("impact_plain") or impact_plain,
        "evidence": ev,
        "confidence": item.get("confidence") or "high",
        "fix": item.get("fix"),
        "question": item.get("question"),
        "status": status,
        "last_seen_at": now,
        "last_checked_at": now,
        "source": source,
    }


def _status_on_redetect(current: Optional[str]) -> str:
    """`fixed` seen again => `regressed`; anything else keeps its status."""
    return "regressed" if current == "fixed" else (current or "open")


async def upsert_item(db, *, user_id: str, project_id: str, item: dict,
                      source: str = "checkup") -> str:
    """Insert/refresh one item.  Never downgrades a fixed/ignored row:
    re-detection of a `fixed` item becomes `regressed`; `ignored` stays
    ignored.  Returns the resulting status."""
    finding_id = _item_identity(item)[3]
    key = {"user_id": user_id, "project_id": project_id, "finding_id": finding_id}
    now = _now()
    existing = await db.cto_open_findings.find_one(key, projection={"status": 1})
    cur = normalize_status(existing.get("status")) if existing else None
    if cur == "ignored":
        return "ignored"
    status = _status_on_redetect(cur)
    await db.cto_open_findings.update_one(
        key,
        {"$setOnInsert": {**key, "first_seen_at": now, "found_at": now},
         "$set": _item_fields(item, status, now, source)},
        upsert=True,
    )
    return status


async def set_status(db, *, user_id: str, project_id: str, item_id: str,
                     new_status: str, fixed_by: Optional[str] = None) -> bool:
    """Guarded status change.  'fixed' requires the caller to have
    re-run the check (S8) — enforced by passing `fixed_by`."""
    if new_status not in STATUSES:
        return False
    key = {"user_id": user_id, "project_id": project_id, "finding_id": item_id}
    row = await db.cto_open_findings.find_one(key, projection={"status": 1})
    if not row or not can_transition(row.get("status"), new_status):
        return False
    if new_status == "fixed" and not fixed_by:
        return False
    patch: dict[str, Any] = {"status": new_status, "last_checked_at": _now()}
    if new_status == "fixed":
        patch.update({"fixed_at": _now(), "fixed_by": fixed_by})
    await db.cto_open_findings.update_one(key, {"$set": patch})
    return True


async def list_items(db, *, user_id: str, project_id: str,
                     include_closed: bool = False) -> list[dict]:
    q: dict[str, Any] = {"user_id": user_id, "project_id": project_id}
    docs = []
    async for d in db.cto_open_findings.find(q, {"_id": 0}):
        docs.append(d)
    items = [to_fix_item(d) for d in docs]
    if not include_closed:
        items = [i for i in items if i["status"] in ("open", "your_answer", "fixing", "regressed")]
    return items


# ── Checkup records + funnel events ───────────────────────────────────
FUNNEL_EVENTS = (
    "checkup_started", "checkup_verdict_shown", "fix_clicked", "fix_applied",
    "fix_verified", "item_ignored", "question_answered", "item_regressed",
    "checkup_skipped",
)


async def record_event(db, *, user_id: str, project_id: Optional[str],
                       event: str, meta: Optional[dict] = None) -> bool:
    """Funnel analytics.  Never raises — analytics must not break a user flow."""
    if event not in FUNNEL_EVENTS:
        return False
    try:
        await db.checkup_events.insert_one({
            "user_id": user_id, "project_id": project_id, "event": event,
            "meta": meta or {}, "at": _now(),
        })
        return True
    except Exception as e:                              # noqa: BLE001
        logger.warning("[fix_items] event %s dropped: %r", event, e)
        return False


async def save_checkup(db, *, user_id: str, project_id: str, items: list[dict],
                       duration_ms: int, stack: Optional[str] = None) -> dict:
    record = {
        "user_id": user_id, "project_id": project_id, "created_at": _now(),
        "score": score_for(items), "stages": stage_summary(items),
        "item_count": len(items), "duration_ms": int(duration_ms), "stack": stack,
    }
    await db.project_checkups.insert_one(dict(record))
    return record


async def ensure_flags(db) -> int:
    """Register the checkup flags as OFF if missing.  Never changes an
    existing flag, so a flag an admin already turned on stays on and an
    off flag can never be flipped by calling this."""
    created = 0
    for flag, description in CHECKUP_FLAGS.items():
        res = await db.feature_flags.update_one(
            {"flag": flag},
            {"$setOnInsert": {
                "flag": flag, "enabled": False, "tier_allowlist": [],
                "user_allowlist": [], "rollout_pct": 0, "description": description,
            }},
            upsert=True,
        )
        if getattr(res, "upserted_id", None) is not None:
            created += 1
    return created
