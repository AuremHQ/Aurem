"""
services/project_onboarding_scan.py — automatic background deep scan
on project connect.

2026-08-22 — closes a gap in the Prompt Starter panel's personalized
"FROM YOUR REPO" suggestions (routers/findings.py::starter_suggestions):
the only paths that ever wrote to `cto_open_findings` were a Loop Ship
run (services/loop_full_scan.py) or the founder-only manual
POST /codebase-health/scan endpoint. A user landing on the empty chat
right after connecting a project — exactly when the "what do I even
type?" onboarding problem happens — had nothing personalized to show
yet.

Runs the SAME scanner pipeline codebase_health.py exposes manually —
iterates `SCANNERS.values()` directly, which is all 16 categories
(the original 7 plus the 9 "Launch-ready X / 16" categories merged in
via `services.codebase_health_extra.EXTRA_SCANNERS` at import time,
see codebase_health_core.py's own module-level `SCANNERS.update(...)`)
— as a fire-and-forget background task, gated per project via
(user_id, project_id) exactly like every other cto_open_findings
writer. No tier/founder restriction here — this is an internal system
task triggered once on connect, not the founder-only manual /scan
endpoint (which stays admin-gated, unchanged).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ONBOARDING_SCAN_SOURCE = "onboarding_auto_scan"


async def run_onboarding_scan(
    *, db, user_id: str, project_id: str,
    github_token: str, github_owner: str, github_repo: str,
) -> None:
    """Fire-and-forget: scan a freshly-connected repo and persist
    critical/high findings to `cto_open_findings`. Errors are logged
    and swallowed — must never affect the project-add response or
    crash the caller's background task group (same contract as
    `routers/cto_projects.py::_run_project_indexing`)."""
    try:
        if not (github_token and github_owner and github_repo):
            return
        # Master Prompt v2 PART 4.1 — one free full scan per project
        # per account. start_free_scan is the atomic guard: a repeat
        # fire (retry, race) for the SAME project_id is a no-op.
        from services.free_scan_results import mark_free_scan_error, start_free_scan
        started = await start_free_scan(db, user_id=user_id, project_id=project_id)
        if not started:
            return
        try:
            # Iter arch-2a — SCANNERS now lives in services/codebase_health_core.py
            # (fixed half of this service→router violation). `_build_text_cache`
            # still comes from the router — it depends on GitHub-tree-walking
            # primitives (_list_repo_tree_with_sha/_fetch_file) that are
            # themselves owned by routers/security_scan.py pending a larger,
            # separately-scoped extraction (tracked, not silently patched).
            from services.codebase_health_core import (
                SCANNERS,
                _score_for_findings,
                resolve_live_cves,
            )
            from services.repo_text_cache import build_text_cache as _build_text_cache
            text_cache = await _build_text_cache(github_owner, github_repo, github_token)
            if not text_cache:
                await mark_free_scan_error(db, project_id=project_id)
                return

            # Ticket B (2026-02) — this call site is async but (unlike the
            # router's /scan endpoint) calls every SCANNERS[cat] directly
            # on the event loop, not via asyncio.to_thread. Without this,
            # the live OSV cache is never warmed here and onboarding scans
            # stay on the static `_CVE_DB` forever. resolve_live_cves never
            # raises (catches its own OSV failures internally), so no extra
            # try/except needed beyond this function's existing outer one.
            await resolve_live_cves(text_cache)

            # Master Prompt v2 PART 4.1 — cost-capped: a (today purely
            # forward-looking, since all 16 categories are free static
            # scans) per-category cost estimate scaled by repo size, so a
            # pathologically huge repo degrades gracefully (remaining
            # categories marked "not run") instead of ever failing the
            # whole scan or running away on cost.
            from config.free_scan import (
                ESTIMATED_COST_PER_FILE_PER_CATEGORY_USD,
                FREE_SCAN_COST_CAP_USD,
            )
            from services.free_scan_results import (
                finish_free_scan,
                run_categories_with_cost_cap,
            )
            cost_per_category = len(text_cache) * ESTIMATED_COST_PER_FILE_PER_CATEGORY_USD
            all_findings, categories_run, categories_not_run, cost_usd = run_categories_with_cost_cap(
                SCANNERS, text_cache, cap_usd=FREE_SCAN_COST_CAP_USD,
                cost_per_category_usd=cost_per_category,
            )

            from services.loop_full_scan import persist_findings_to_backlog
            written = await persist_findings_to_backlog(
                db, user_id=user_id, project_id=project_id,
                findings=all_findings, scan_source=ONBOARDING_SCAN_SOURCE,
            )
            # Ticket E (2026-02) — per-project health-trend snapshot. The
            # onboarding scan never computed an overall score before this
            # (it only ever wrote critical/high findings to the backlog);
            # reusing the SAME _score_for_findings every other scan path
            # uses, not a new formula.
            from services.project_health_trend import record_health_snapshot
            onboarding_score = _score_for_findings(all_findings)
            await record_health_snapshot(
                db, project_id=project_id, user_id=user_id,
                score=onboarding_score, findings=all_findings,
                source="onboarding_scan",
            )
            # Master Prompt v2 PART 4.1 — the full-severity results doc
            # the free-scan RESULTS page reads (cto_open_findings above
            # only keeps critical/high/medium/unknown; this keeps every
            # severity, plus the cost/alarm/categories-not-run bookkeeping).
            await finish_free_scan(
                db, user_id=user_id, project_id=project_id, score=onboarding_score,
                findings=all_findings, categories_run=categories_run,
                categories_not_run=categories_not_run, cost_usd=cost_usd,
            )
        except Exception:
            await mark_free_scan_error(db, project_id=project_id)
            raise
        logger.info(
            "[onboarding-scan] project=%s scanned %d files, wrote %d "
            "critical/high findings",
            project_id, len(text_cache), written,
        )
    except Exception as e:
        logger.warning(
            "[onboarding-scan] failed for project=%s: %r", project_id, e,
        )
