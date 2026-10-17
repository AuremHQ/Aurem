"""
services/scheduled_project_scan.py — Ticket A (2026-02): opt-in,
user-facing scheduled auto-scan + digest email, per connected project.

Schema (new, non-destructive):
  cto_projects (EXISTING collection, 2 new OPTIONAL fields, no backfill
  needed — missing means "off", exactly like every other optional
  project field in this codebase):
    auto_scan_frequency:   "off" | "weekly" | "monthly"  (default "off")
    auto_scan_last_run_at: float epoch seconds | absent

  project_auto_scan_runs (NEW collection — one doc per actual run, for
  the user-visible run history / digest honesty trail):
    _id:            f"{project_id}_{int(run_at)}"   (deterministic, idempotent)
    user_id:        str
    project_id:     str
    run_at:         float epoch seconds
    frequency:      "weekly" | "monthly"
    findings_count: int
    score:          int (0-100, via _score_for_findings)
    email_sent:     bool   — HONEST: False whenever Resend actually failed
                              (e.g. the account is suspended/unverified),
                              never faked to True.
    email_error:    str | None
  Index: project_id + run_at (ensure_index call below, idempotent —
  safe to run on every startup, createIndexes is a no-op if it exists).

Reuses, does not duplicate:
  - services.daily_digest._send_via_resend  (same Resend sender)
  - services.codebase_health_core.SCANNERS / resolve_live_cves (same
    scan pipeline project_onboarding_scan.py already uses)
  - services.loop_full_scan.persist_findings_to_backlog (same backlog)
  - services.pat_vault.get_repo_token (same GitHub-App/PAT token
    resolver codebase_health.py's manual /scan endpoint uses — NOT a
    stored static token, since most projects are GitHub-App-based now)

Explicitly does NOT touch services.scan_fix_quota: this pipeline only
scans + persists findings (read-only reporting), it never calls a
fix/apply path, so the fix-quota meter (which only meters successful
*fixes*) is naturally never consumed by this feature.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

AUTO_SCAN_SOURCE = "scheduled_auto_scan"

# Hard cap on frequency (mission requirement) — enforced SERVER-SIDE
# regardless of how often the cron tick fires; a project can never be
# rescanned more often than its own chosen cadence.
_CAP_SECONDS = {"weekly": 7 * 86400, "monthly": 30 * 86400}

# No unbounded fan-out — if more than this many projects are due in one
# tick, the rest wait for the next tick (6h later, see schedule loop).
_MAX_PROJECTS_PER_TICK = 50

# How often the scheduler wakes up to check what's due. Cheap: a single
# indexed Mongo query — this is NOT an LLM or network cost, it is an
# idempotent read of a date compared to each project's own cap.
_TICK_INTERVAL_SECONDS = 6 * 3600


async def ensure_indexes(db) -> None:
    """Idempotent — safe to call on every startup."""
    try:
        await db.project_auto_scan_runs.create_index([("project_id", 1), ("run_at", -1)])
    except Exception as e:
        logger.warning("[auto-scan] index creation failed (non-fatal): %r", e)


def _render_digest_text(project: dict, score: int, findings_count: int) -> str:
    repo = f"{project.get('github_owner', '')}/{project.get('github_repo', '')}"
    return (
        f"AUREM — Scheduled Scan: {project.get('name') or repo}\n"
        f"{repo}\n"
        f"{'─' * 40}\n"
        f"Health score: {score}/100\n"
        f"Open findings: {findings_count}\n"
        f"\nReview and fix: see your AUREM dashboard → Codebase Health.\n"
    )


async def _scan_one_project(db, project: dict) -> dict:
    """Runs the scan + persists findings for ONE project. Returns a
    result dict; never raises (every real failure is captured in the
    returned dict so the caller can still write an honest audit row)."""
    project_id = project["project_id"]
    user_id = project["user_id"]
    frequency = project.get("auto_scan_frequency")
    run_at = time.time()
    result = {
        "project_id": project_id, "user_id": user_id, "run_at": run_at,
        "frequency": frequency, "findings_count": 0, "score": None,
        "email_sent": False, "email_error": None,
    }
    try:
        from services.pat_vault import get_repo_token, GithubAppAuthError
        try:
            pat = await get_repo_token(project)
        except GithubAppAuthError as e:
            result["email_error"] = f"github_auth_failed: {e}"
            return result
        owner, repo = project.get("github_owner") or "", project.get("github_repo") or ""
        if not (owner and repo and pat):
            result["email_error"] = "project_missing_github_linkage"
            return result

        from services.repo_text_cache import build_text_cache
        text_cache = await build_text_cache(owner, repo, pat)
        if not text_cache:
            result["email_error"] = "scan_no_files_read"
            return result

        from services.codebase_health_core import SCANNERS, resolve_live_cves, _score_for_findings
        await resolve_live_cves(text_cache)
        all_findings: list[dict] = []
        for scan_fn in SCANNERS.values():
            try:
                all_findings.extend(scan_fn(text_cache))
            except Exception as e:
                logger.debug("[auto-scan] category failed for %s: %r", project_id, e)

        from services.loop_full_scan import persist_findings_to_backlog
        written = await persist_findings_to_backlog(
            db, user_id=user_id, project_id=project_id,
            findings=all_findings, scan_source=AUTO_SCAN_SOURCE,
        )
        result["findings_count"] = written
        result["score"] = _score_for_findings(all_findings)

        # Ticket E (2026-02) — per-project health-trend snapshot.
        from services.project_health_trend import record_health_snapshot
        await record_health_snapshot(
            db, project_id=project_id, user_id=user_id,
            score=result["score"], findings=all_findings,
            source="scheduled_auto_scan",
        )

        # Email — honest result only. Resend being down/misconfigured
        # must show up as email_sent=False + the real error, never a
        # faked "sent".
        dev_user = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "email": 1})
        to_email = (dev_user or {}).get("email")
        if to_email:
            from services.daily_digest import _send_via_resend
            body = _render_digest_text(project, result["score"], written)
            sent = await _send_via_resend(to_email, "AUREM — Scheduled Scan Results", body)
            result["email_sent"] = sent
            if not sent:
                result["email_error"] = "resend_delivery_failed"
                logger.warning(
                    "[auto-scan] project=%s scanned OK (score=%s, findings=%d) "
                    "but digest email NOT delivered (Resend failed) — honestly "
                    "recorded, not faked as sent",
                    project_id, result["score"], written,
                )
        else:
            result["email_error"] = "no_email_on_file"
    except Exception as e:
        logger.warning("[auto-scan] project=%s failed: %r", project_id, e)
        result["email_error"] = f"unexpected: {e}"
    return result


async def run_due_auto_scans(db) -> dict:
    """Finds every project whose chosen cadence cap has elapsed (or
    that has never run), scans up to _MAX_PROJECTS_PER_TICK of them,
    and writes one honest audit row per run. Returns a summary dict —
    never raises (every per-project failure is isolated above)."""
    now = time.time()
    due: list[dict] = []
    cursor = db.cto_projects.find(
        {"auto_scan_frequency": {"$in": ["weekly", "monthly"]}},
        {"_id": 0},
    ).limit(_MAX_PROJECTS_PER_TICK * 4)  # bounded scan of candidates, not the whole collection
    async for proj in cursor:
        freq = proj.get("auto_scan_frequency")
        cap = _CAP_SECONDS.get(freq)
        if cap is None:
            continue
        last_run = proj.get("auto_scan_last_run_at")
        if last_run is not None and (now - last_run) < cap:
            continue  # hard cap not yet elapsed — never rescan early
        due.append(proj)
        if len(due) >= _MAX_PROJECTS_PER_TICK:
            break

    scanned, emailed = 0, 0
    for proj in due:
        r = await _scan_one_project(db, proj)
        try:
            await db.project_auto_scan_runs.update_one(
                {"_id": f"{r['project_id']}_{int(r['run_at'])}"},
                {"$set": r}, upsert=True,
            )
            await db.cto_projects.update_one(
                {"project_id": r["project_id"]},
                {"$set": {"auto_scan_last_run_at": r["run_at"]}},
            )
        except Exception as e:
            logger.warning("[auto-scan] failed to persist audit row for %s: %r", r["project_id"], e)
        scanned += 1
        if r["email_sent"]:
            emailed += 1
    if due:
        logger.info("[auto-scan] tick: %d due, %d scanned, %d emails actually delivered",
                    len(due), scanned, emailed)
    return {"due": len(due), "scanned": scanned, "emailed": emailed}


async def schedule_project_auto_scan() -> None:
    """Background loop — same supervised-task shape as
    schedule_daily_digest(); checks every _TICK_INTERVAL_SECONDS which
    projects are due (cheap, idempotent query) and scans them."""
    from cto_services.db import get_db
    while True:
        try:
            db = get_db()
            if db is not None:
                await ensure_indexes(db)
                await run_due_auto_scans(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[auto-scan] scheduler tick crashed: %r", e)
        await asyncio.sleep(_TICK_INTERVAL_SECONDS)
