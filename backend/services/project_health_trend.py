"""
services/project_health_trend.py — Ticket E (2026-02): per-project
health score + trend.

IMPORTANT distinction (per mission) — services/health_score.py scores
AUREM's OWN platform (9 founder categories: security, CI pass-rate,
AUREM's own incident log, etc.) via AUREM-internal collections like
synthetic_checks/architecture_review_log/backup_history. None of that
applies to a CUSTOMER's connected repo. This module is a completely
separate, much simpler scorer: one number per scan, computed from that
PROJECT's own scan findings via the EXISTING
`codebase_health_core._score_for_findings` (same function Tickets A/C
already reuse) — no new scoring formula invented.

SCHEMA (new collection, non-destructive — shown here before any write
code runs, per mission's "show me the schema before you write the
migration; if destructive, STOP" instruction. This is NOT destructive:
brand-new collection, zero changes to any existing collection/field,
no backfill of historical data needed or attempted):

  project_health_snapshots:
    _id:                 ObjectId (auto)
    project_id:          str
    user_id:             str            — owner, for per-user isolation
    score:               int 0-100 | None   (None if _score_for_findings
                                              had nothing scoreable, e.g.
                                              a fully-empty findings list
                                              still scores 100 though —
                                              None only if score itself
                                              could not be computed)
    findings_total:       int
    findings_by_severity: {critical: int, high: int, unknown: int,
                            medium: int, low: int, info: int}
    source:               "manual_scan" | "scheduled_auto_scan"
                           | "onboarding_scan"
    snapshot_at:          float (epoch seconds)

  Index: (project_id ASC, snapshot_at DESC) — idempotent create_index,
  safe on every startup; serves both the trend-read query (sorted,
  filtered by project) and the retention-trim query below.

  Retention: after every insert, if a project has more than
  _MAX_SNAPSHOTS_PER_PROJECT (90) snapshots, the oldest ones beyond
  that cap are deleted. Bounds storage growth per project forever —
  no manual cleanup job needed.

Called from every real scan path that already computes a score via
_score_for_findings: routers/codebase_health.py's /scan endpoint,
services/scheduled_project_scan.py's auto-scan (Ticket A), and
services/project_onboarding_scan.py's initial onboarding scan.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_MAX_SNAPSHOTS_PER_PROJECT = 90
_SEV_KEYS = ("critical", "high", "unknown", "medium", "low", "info")


async def ensure_indexes(db) -> None:
    """Idempotent — safe to call on every startup."""
    try:
        await db.project_health_snapshots.create_index(
            [("project_id", 1), ("snapshot_at", -1)]
        )
    except Exception as e:
        logger.warning("[health-trend] index creation failed (non-fatal): %r", e)


def _severity_breakdown(findings: list[dict]) -> dict:
    out = {k: 0 for k in _SEV_KEYS}
    for f in findings:
        sev = (f.get("severity") or "info").lower()
        if sev in out:
            out[sev] += 1
    return out


async def record_health_snapshot(db, *, project_id: str, user_id: str,
                                  score, findings: list[dict], source: str) -> None:
    """Best-effort — a snapshot-write failure must never fail the scan
    that triggered it (same non-fatal contract as every other
    persistence helper in this codebase, e.g. Ticket A/B's cache
    persist)."""
    if db is None:
        return
    try:
        await db.project_health_snapshots.insert_one({
            "project_id": project_id,
            "user_id": user_id,
            "score": score,
            "findings_total": len(findings),
            "findings_by_severity": _severity_breakdown(findings),
            "source": source,
            "snapshot_at": time.time(),
        })
        # Retention cap — keep only the most recent 90 per project.
        cursor = db.project_health_snapshots.find(
            {"project_id": project_id}, {"_id": 1},
        ).sort("snapshot_at", -1).skip(_MAX_SNAPSHOTS_PER_PROJECT)
        old_ids = [d["_id"] async for d in cursor]
        if old_ids:
            await db.project_health_snapshots.delete_many({"_id": {"$in": old_ids}})
    except Exception as e:
        logger.warning("[health-trend] snapshot write failed for project=%s (non-fatal): %r",
                        project_id, e)


async def get_project_health_trend(db, *, project_id: str, user_id: str,
                                    limit: int = _MAX_SNAPSHOTS_PER_PROJECT) -> list[dict]:
    """Per-user isolation — only ever returns snapshots that also
    match the caller's own user_id, even if project_id alone would
    have matched someone else's project."""
    cursor = db.project_health_snapshots.find(
        {"project_id": project_id, "user_id": user_id}, {"_id": 0},
    ).sort("snapshot_at", 1).limit(limit)
    return [doc async for doc in cursor]
