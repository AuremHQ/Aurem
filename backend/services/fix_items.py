"""
services/fix_items.py — the single "things to fix" layer for the
checkup-first experience.

Four systems used to keep their own list of issues (`cto_open_findings`,
`codebase_health` scan results, `first_scan_results`, the chat-side
`save_finding`).  This module is the ONE read/write surface over
`cto_open_findings`; it only ADDS fields, never renames or removes one,
so every existing reader (`routers/findings.py`, `advisor_context`,
`finding_fix_applier`, `mode_d_debugger`) keeps working unchanged.

One score function: `codebase_health_core._score_for_findings`
(`round(100*exp(-raw/60))`).  Nothing here re-implements it.

Everything user-facing is plain English (no rule ids, paths or model
names in `title_plain` / `impact_plain`); raw details stay in `evidence`
for the Developer view.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Feature flags (all default OFF; S1/S13: never auto-enabled) ───────
CHECKUP_FLAGS: dict[str, str] = {
    "checkup_flow": "Checkup-first onboarding: verdict screen after connecting a repo.",
    "permission_map": "Plain-English data access map (who can read/write what).",
    "fix_list_panel": "Single Fix List panel backed by fix_items.",
    "chat_clean_ui": "Calm chat: plain replies, one activity line, no floating helpers.",
    "fix_loop_v2": "Safe fix loop: restore point, branch, PR, proof re-run.",
    "guardian_rescan": "Re-check on every push to catch regressions.",
    "live_read_checks": "Consented, read-only, masked checks against live services.",
}

STAGES = ("runs", "safe", "permissions", "launch")
SEVERITIES = ("critical", "high", "medium", "low", "info")
STATUSES = ("open", "your_answer", "fixing", "fixed", "ignored", "regressed")

# Legacy statuses written by older code map onto the new vocabulary.
_LEGACY_STATUS = {"aged-out": "ignored", "snoozed": "ignored", "dismissed": "ignored"}

# Allowed transitions; anything else is refused so a stale UI cannot
# resurrect a fixed item or mark an unfixed one "fixed" (S8).
_TRANSITIONS: dict[str, set[str]] = {
    "open": {"your_answer", "fixing", "ignored"},
    "your_answer": {"open", "fixing", "ignored"},
    "fixing": {"open", "fixed", "ignored"},
    "fixed": {"regressed"},
    "ignored": {"open"},
    "regressed": {"fixing", "ignored", "open"},
}

_SCANNER_STAGE = {
    "security": "safe",
    "dependencies": "safe",
    "docker": "launch",
    "performance": "launch",
    "database": "permissions",
    "bug_hunt": "runs",
    "code_quality": "runs",
}

_STAGE_TITLES = {
    "runs": "Does it run?",
    "safe": "Is it safe?",
    "permissions": "Who can see what?",
    "launch": "Is it ready to launch?",
}

# Plain-English copy per scanner family (S10).  Rule-specific copy can be
# added to `_RULE_COPY` without touching callers.
_SCANNER_COPY = {
    "security": ("A security weak spot was found",
                 "Someone who shouldn't could get at your app or your users' data."),
    "dependencies": ("An outdated or risky package is in use",
                     "A known flaw in a package your app uses could be abused."),
    "database": ("Your data may be reachable by the wrong people",
                 "Visitors might be able to read or change information they shouldn't."),
    "bug_hunt": ("Part of the app is likely to break",
                 "Users could hit an error or a button that does nothing."),
    "code_quality": ("A part of the code is fragile",
                     "Changes here are more likely to cause surprise bugs."),
    "performance": ("A page may feel slow",
                    "Visitors could leave before the page finishes loading."),
    "docker": ("The way the app is packaged is risky",
               "A launch could fail or expose more than intended."),
}
_RULE_COPY: dict[str, tuple[str, str]] = {}

_RULE_STAGE: dict[str, str] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_status(status: Optional[str]) -> str:
    s = (status or "open").lower()
    s = _LEGACY_STATUS.get(s, s)
    return s if s in STATUSES else "open"


def normalize_severity(sev: Optional[str]) -> str:
    s = (sev or "").lower()
    if s == "warning":
        return "medium"
    return s if s in SEVERITIES else "medium"


def stage_for(scanner: Optional[str], rule_id: Optional[str] = None) -> str:
    if rule_id and rule_id in _RULE_STAGE:
        return _RULE_STAGE[rule_id]
    return _SCANNER_STAGE.get((scanner or "").lower(), "runs")


def plain_copy(scanner: Optional[str], rule_id: Optional[str] = None) -> tuple[str, str]:
    if rule_id and rule_id in _RULE_COPY:
        return _RULE_COPY[rule_id]
    return _SCANNER_COPY.get(
        (scanner or "").lower(),
        ("Something needs a look", "It may cause problems for you or your users."),
    )


def can_transition(current: Optional[str], new: str) -> bool:
    cur = normalize_status(current)
    return cur == new or new in _TRANSITIONS.get(cur, set())


def to_fix_item(doc: dict) -> dict:
    """Project a `cto_open_findings` row (old or new shape) into the
    fix_items shape.  Pure; never mutates the input; tolerates rows that
    pre-date the additive fields."""
    scanner = doc.get("scanner")
    rule_id = doc.get("rule_id")
    title_plain, impact_plain = plain_copy(scanner, rule_id)
    return {
        "id": doc.get("finding_id") or "",
        "project_id": doc.get("project_id"),
        "category": doc.get("category") or stage_for(scanner, rule_id),
        "stage": doc.get("stage") or stage_for(scanner, rule_id),
        "severity": normalize_severity(doc.get("severity")),
        "title_plain": doc.get("title_plain") or title_plain,
        "impact_plain": doc.get("impact_plain") or impact_plain,
        "evidence": doc.get("evidence") or {
            "file": doc.get("file") or "",
            "line": doc.get("line") or 0,
            "rule": rule_id or "",
            "detail": doc.get("message") or doc.get("title") or "",
        },
        "confidence": doc.get("confidence") or "high",
        "fix": doc.get("fix") or ({"hint": doc["fix_hint"]} if doc.get("fix_hint") else None),
        "status": normalize_status(doc.get("status")),
        "question": doc.get("question"),
        "found_at": doc.get("found_at") or doc.get("first_seen_at"),
        "fixed_at": doc.get("fixed_at"),
        "fixed_by": doc.get("fixed_by"),
        "last_checked_at": doc.get("last_checked_at") or doc.get("last_seen_at"),
        "rule_id": rule_id,
    }


def score_for(items: list[dict]) -> int:
    """Health score of the still-open items — the one score function."""
    from services.codebase_health_core import _score_for_findings
    live = [
        {"severity": normalize_severity(i.get("severity"))}
        for i in items
        if normalize_status(i.get("status")) in ("open", "your_answer", "fixing", "regressed")
    ]
    return _score_for_findings(live)


def stage_summary(items: list[dict]) -> list[dict]:
    """Per-stage verdict used by the checkup screen: ok/attention/problem."""
    out = []
    for stage in STAGES:
        mine = [
            i for i in items
            if (i.get("stage") or i.get("category")) == stage
            and normalize_status(i.get("status")) in ("open", "your_answer", "fixing", "regressed")
        ]
        if any(normalize_severity(i.get("severity")) == "critical" for i in mine):
            verdict = "problem"
        elif any(normalize_severity(i.get("severity")) == "high" for i in mine):
            verdict = "attention"
        elif mine:
            verdict = "attention"
        else:
            verdict = "ok"
        out.append({"stage": stage, "title": _STAGE_TITLES[stage],
                    "verdict": verdict, "open_count": len(mine)})
    return out


# ── Persistence (additive over cto_open_findings) ─────────────────────
async def upsert_item(db, *, user_id: str, project_id: str, item: dict,
                      source: str = "checkup") -> str:
    """Insert/refresh one item.  Never downgrades a fixed/ignored row:
    re-detection of a `fixed` item becomes `regressed`; `ignored` stays
    ignored.  Returns the resulting status."""
    scanner = item.get("scanner") or "checkup"
    rule_id = item.get("rule_id") or "unknown"
    file_ = (item.get("evidence") or {}).get("file") or item.get("file") or ""
    line_ = int((item.get("evidence") or {}).get("line") or item.get("line") or 0)
    finding_id = item.get("id") or f"{scanner}::{file_}:{line_}:{rule_id}"
    key = {"user_id": user_id, "project_id": project_id, "finding_id": finding_id}
    now = _now()
    existing = await db.cto_open_findings.find_one(key, projection={"status": 1})
    cur = normalize_status((existing or {}).get("status")) if existing else None
    if cur == "ignored":
        return "ignored"
    status = "regressed" if cur == "fixed" else (cur or "open")
    title_plain, impact_plain = plain_copy(scanner, rule_id)
    await db.cto_open_findings.update_one(
        key,
        {
            "$setOnInsert": {**key, "first_seen_at": now, "found_at": now},
            "$set": {
                "scanner": scanner,
                "rule_id": rule_id,
                "severity": normalize_severity(item.get("severity")),
                "file": file_,
                "line": line_,
                "category": item.get("category") or stage_for(scanner, rule_id),
                "stage": item.get("stage") or stage_for(scanner, rule_id),
                "title_plain": item.get("title_plain") or title_plain,
                "impact_plain": item.get("impact_plain") or impact_plain,
                "evidence": item.get("evidence") or {},
                "confidence": item.get("confidence") or "high",
                "fix": item.get("fix"),
                "question": item.get("question"),
                "status": status,
                "last_seen_at": now,
                "last_checked_at": now,
                "source": source,
            },
        },
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


async def reconcile(db, *, user_id: str, project_id: str, detected_ids: set[str],
                    unverified_stages: set[str]) -> list[str]:
    """Guardian re-scan: after a fresh checkup, an item the checkup used to
    report that no longer shows up is FIXED — the re-run itself is the proof
    (S8).  Stages whose checks could not run are never closed (S9), and rows
    from other writers (not `source == "checkup"`) are left alone."""
    closed: list[str] = []
    q = {"user_id": user_id, "project_id": project_id, "source": "checkup",
         "status": {"$in": ["open", "your_answer", "fixing"]}}
    async for d in db.cto_open_findings.find(q, {"_id": 0, "finding_id": 1, "stage": 1}):
        fid = d.get("finding_id")
        if fid in detected_ids or (d.get("stage") or "") in unverified_stages:
            continue
        await db.cto_open_findings.update_one(
            {"user_id": user_id, "project_id": project_id, "finding_id": fid},
            {"$set": {"status": "fixed", "fixed_at": _now(), "fixed_by": "proof_rerun",
                      "last_checked_at": _now()}})
        closed.append(fid)
    return closed


async def funnel_summary(db, *, start: datetime, end: datetime) -> dict:
    """Admin: distinct users per checkup funnel event in [start, end] and the
    step-to-step conversion, so 'does the checkup get people to a fix?' is a
    number rather than a feeling."""
    users: dict[str, set] = {e: set() for e in FUNNEL_EVENTS}
    async for d in db.checkup_events.find({"at": {"$gte": start, "$lte": end}},
                                          {"_id": 0, "user_id": 1, "event": 1}):
        if d.get("event") in users and d.get("user_id"):
            users[d["event"]].add(d["user_id"])
    counts = {e: len(u) for e, u in users.items()}

    def rate(num: str, den: str) -> Optional[float]:
        return round(100 * counts[num] / counts[den], 1) if counts[den] else None
    return {
        "users": counts,
        "rates": {
            "verdict_shown_of_started": rate("checkup_verdict_shown", "checkup_started"),
            "fix_clicked_of_verdict": rate("fix_clicked", "checkup_verdict_shown"),
            "fix_applied_of_clicked": rate("fix_applied", "fix_clicked"),
            "fix_verified_of_applied": rate("fix_verified", "fix_applied"),
            "skipped_of_started": rate("checkup_skipped", "checkup_started"),
        },
    }
