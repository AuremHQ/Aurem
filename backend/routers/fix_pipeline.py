"""
routers/fix_pipeline.py — Iter 212m-121

Bulk + streaming fix orchestrator on top of the EXISTING real fix
pipeline (services/finding_fix_applier.apply_finding_fix).  Zero
mocks — every commit_sha returned here is a real GitHub commit
because we delegate to the same code path the per-finding `/fix`
endpoint already uses (which writes through services/
github_api_writer.commit_files()).

Endpoints (all under /api/aurem-dev):
  POST /fix-pipeline/preview
       Body: {project_id, finding_ids OR category, scanner}
       Returns: {count, tokens_cost, usd_cost, is_unlimited, balance}

  POST /fix-pipeline/bulk
       Body: same as preview, plus optional `findings:[...]` so the
       caller can stream the exact normalised finding objects (the
       Vanguard scan response shape).  Kicks off a background task
       that runs the fixes sequentially.  Returns `{job_id}`.

  GET  /fix-pipeline/stream/{job_id}
       Server-Sent Events stream.  Phases emitted, in order, per
       finding:
         queued      — about to start this finding
         reading     — fetching file from GitHub
         generating  — LLM is producing the patched content
         committing  — pushing the commit
         verifying   — re-running the static scanner
         fix-done    — single finding terminal (ok + commit OR error)
       And then once across the whole job:
         done        — terminal summary
         heartbeat   — sent every 120 s if otherwise idle

  GET  /fix-pipeline/summary/{job_id}
       Polling fallback for clients that can't use SSE (returns the
       same data the final `done` event carries).

Cost model:
  • Single token  ≈ $0.0001 USD  (constant TOKEN_USD_RATE).  Adjust
    in one place when pricing changes.  Founders / admins / unlimited
    pay zero, so they see "⚡ FREE" in the UI.
  • Per-finding price comes from `category_token_cost()` — uses the
    existing per-category cost table in routers/codebase_health.py
    (security/perf/quality/deps/db = 5, bug_hunt = 8).  Vanguard
    findings inherit from their normalised vuln class.
"""
# arch: allow-http — Streams provider callbacks (iter 212m-225)
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from sse_starlette.sse import EventSourceResponse

from cto_services.auth import current_dev
from cto_services.db import get_db
from services import fix_job_manager as fjm
from services import ora_fix_learning
from services.finding_fix_applier import (
    apply_file_fixes,
    apply_finding_fix,
    revert_batch,
    revert_finding_fix,
)
from services.rate_limiter import check_rate_limit_async
from services.fixed_findings import record_fixed
from services.notifications import emit_notification
from services.scan_fix_quota import (
    ALL_FIX_TOOLS,
    assert_can_fix,
    get_fix_quota,
    record_scan_fixes,
)
from services.team_permissions import check_permission

logger = logging.getLogger("aurem-dev.fix_pipeline")
router = APIRouter(prefix="/fix-pipeline", tags=["Fix Pipeline"])


# ─── Iter 212m-147 — unified-diff helper ─────────────────────────────
#
# Produces the SSE `diff` event payload from the before/after file
# contents.  We use Python's stdlib `difflib.unified_diff` and strip
# the standard `--- /+++ /@@@` chrome lines, leaving only the actual
# `+`/`-` lines (with a small amount of `context` lines so the
# drawer can render a sensible window).  Limits the output to
# `_MAX_DIFF_LINES` so a 5000-line refactor doesn't blast the SSE
# pipe; the drawer can scroll within what it gets.
_MAX_DIFF_LINES = 80


def _compute_diff_lines(before: str, after: str) -> list[dict]:
    # Part 2b R3 — this diff streams directly over SSE to the
    # browser (the "fix-diff" event). A fix for a hardcoded secret
    # would otherwise show the raw secret value in the "remove" line.
    from services.secret_masking import redact_secrets_in_text
    if before == after:
        return []
    before_lines = before.splitlines(keepends=False)
    after_lines  = after.splitlines(keepends=False)
    out: list[dict] = []
    for line in difflib.unified_diff(
        before_lines, after_lines, n=2, lineterm="",
    ):
        if not line:
            continue
        # Skip the `--- `/`+++ `/`@@` header chrome.
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if line.startswith("@@"):
            out.append({"type": "hunk", "line": line})
            continue
        if line.startswith("-"):
            out.append({"type": "remove", "line": redact_secrets_in_text(line[1:])})
        elif line.startswith("+"):
            out.append({"type": "add", "line": redact_secrets_in_text(line[1:])})
        else:
            # Context line (no prefix marker after the unified_diff
            # strip).  Useful so the drawer can show a few lines of
            # surrounding code for orientation.
            stripped = line[1:] if line.startswith(" ") else line
            out.append({"type": "context", "line": redact_secrets_in_text(stripped)})
        if len(out) >= _MAX_DIFF_LINES:
            out.append({"type": "context",
                        "line": f"... diff truncated at {_MAX_DIFF_LINES} lines"})
            break
    return out


# ─── Pipeline pacing ───────────────────────────────────────────────────
# Iter 212m-127 — Batched bulk-fix execution.  Findings are
# processed in chunks of _BULK_BATCH_SIZE, each chunk interleaved
# across severity buckets so the user sees a critical, a high,
# a medium and a low fix landing in every batch rather than the
# scanner's natural critical-first ordering.  Between batches the
# worker awaits a small breather to let GitHub's branch indexer
# catch up — keeps PR creation reliable.
_BULK_BATCH_SIZE          = 10
_INTER_BATCH_BREATHE_S    = 1.5
_SEVERITY_BUCKET_ORDER    = ("critical", "high", "medium", "low")

# Iter 212m-128 — Auto-restart on per-finding failure.  We attempt
# each finding up to _MAX_FIX_ATTEMPTS times with exponential
# backoff between attempts.  Certain error codes are TERMINAL and
# bypass the retry loop because no amount of retrying will help:
#   • github_credentials_missing — no token to push with
#   • github_unauthorized        — token is bad, won't auto-fix here
#   • insufficient_tokens*       — wallet empty, retry won't help
#   • file_too_large             — won't fit in the LLM context
# Everything else (LLM hallucinated a no-op patch, network blip,
# unhandled exception, transient GitHub 5xx) IS retried.
_MAX_FIX_ATTEMPTS         = 3
_RETRY_BACKOFFS_S         = (1.0, 2.5, 5.0)
# Iter 212m-178 — gap between consecutive GitHub-mutating fixes so a
# bulk run doesn't trip GitHub's secondary (burst) rate limit.
_BULK_INTER_FIX_DELAY_S   = 1.5
# Iter 212m-179 — HARD cap per bulk run.  Each fix costs ~6 GitHub
# content-generating calls (branch ref + blob + tree + commit + ref
# patch + draft PR) against GitHub's secondary limits (80 writes/min,
# 500 writes/hr per user).  Value set from the empirical probe on
# TJSNDHU/Aurem (test_reports/prod_aggression/ratelimit_probe.py).
_BULK_MAX_FINDINGS        = 20
_TERMINAL_ERROR_CODES     = frozenset({
    "github_credentials_missing",
    "github_unauthorized",
    "insufficient_tokens",
    "insufficient_tokens_midbatch",
    "file_too_large",
})


# 2026-09-26 P0 RBAC fix (Task 2) — every handler below used to read/
# write fix-pipeline jobs and quota keyed on `user["user_id"]` (the
# CALLER's own login), never on the team's `effective_account_id`.
# A team member invited with real "loop" access got back EMPTY job
# lists (their own user_id has no jobs — the owner's does) instead of
# a 403, indistinguishable from "nothing here"; a "viewer" (read-only)
# team member could kick off real GitHub-mutating bulk fixes via
# /bulk with ZERO permission enforcement at all. This helper is the
# one place every handler now goes through: resolves the real
# team-permission gate, then hands back a `user`-shaped dict with
# `user_id` swapped to the effective account so quota/job lookups
# hit the SAME data the owner and every other team member share —
# `get_fix_quota()`/`assert_can_fix()` then resolve tier/is_unlimited
# off THAT id, so a team member's fix draws from the shared account's
# quota, not their own (usually free-tier) personal login.
async def _gate(authorization: Optional[str], level: str, db=None) -> tuple[dict, dict]:
    caller = await current_dev(authorization)
    ctx = await check_permission(caller, "loop", level, db=db)
    effective_user = {**caller, "user_id": ctx["effective_account_id"]}
    return caller, effective_user


# ─── Preview endpoint ──────────────────────────────────────────────────
@router.post("/preview")
async def preview_cost(body: dict,
                       authorization: Optional[str] = Header(None)) -> dict:
    """Cheap, no-side-effect cost preview for the bulk-fix confirm
    modal.  Returns total tokens + estimated USD + the caller's
    current balance + an `is_unlimited` flag the UI uses to swap the
    payment line for an `⚡ Founder — FREE` chip."""
    user, effective = await _gate(authorization, "write")
    user_id = effective["user_id"]
    findings = body.get("findings") or []
    if not isinstance(findings, list):
        raise HTTPException(400, "findings must be a list")
    if not findings:
        raise HTTPException(400, "findings is empty")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    # Iter 212m-179 — preview prices only what one run may execute.
    total_requested = len(findings)
    findings = findings[:_BULK_MAX_FINDINGS]

    # Iter 212m-190 — task-quota model: 1 issue fixed = 1 task.
    tool = (body.get("tool") or "health-scan").strip()
    if tool not in ALL_FIX_TOOLS:
        tool = "health-scan"
    quota = await get_fix_quota(effective)
    is_unlim = quota["is_unlimited"]
    count = len(findings)
    tool_allowed = tool in quota["fix_tools"]
    bulk_allowed = count <= 1 or quota["bulk_fix"]
    remaining = quota["tasks_remaining"]
    enough_tasks = remaining is None or count <= remaining
    can_proceed = tool_allowed and bulk_allowed and enough_tasks
    reason = None
    if not tool_allowed:
        reason = "fix_not_available_on_tier"
    elif not bulk_allowed:
        reason = "bulk_fix_not_available"
    elif not enough_tasks:
        reason = "insufficient_tasks"

    return {
        "ok":              True,
        "count":           count,
        "bulk_max":        _BULK_MAX_FINDINGS,
        "total_requested": total_requested,
        "is_unlimited": is_unlim,
        # Task-quota fields — 1 task per fix, flat.
        "tool":               tool,
        "tier":               quota["tier"],
        "tasks_needed":       count,
        "tasks_remaining":    remaining,
        "monthly_task_limit": quota["monthly_task_limit"],
        "tool_allowed":       tool_allowed,
        "bulk_allowed":       bulk_allowed,
        "can_proceed":        can_proceed,
        "reason":             reason,
        "shortfall":          0 if enough_tasks else count - (remaining or 0),
    }


@router.get("/quota")
async def fix_quota(authorization: Optional[str] = Header(None)) -> dict:
    """Iter 212m-190 — task-quota snapshot for the fix UI. The frontend
    uses `fix_tools` to show/hide per-finding Fix buttons and
    `bulk_fix` to show/hide the bulk button (Team tier only)."""
    _, effective = await _gate(authorization, "read")
    q = await get_fix_quota(effective)
    return {"ok": True, **q}


# ─── Bulk fix kick-off ─────────────────────────────────────────────────
@router.post("/bulk")
async def start_bulk_fix(body: dict,
                         authorization: Optional[str] = Header(None)) -> dict:
    """Kick off a sequential bulk fix.  The HTTP response returns
    immediately with `{job_id}`; the client opens an SSE connection
    to `/fix-pipeline/stream/{job_id}` to watch progress."""
    _, effective = await _gate(authorization, "write")
    user_id   = effective["user_id"]
    project_id = (body or {}).get("project_id")
    finding_ids = (body or {}).get("finding_ids") or []
    findings   = body.get("findings") or []
    if not project_id:
        raise HTTPException(400, "project_id required")
    if not finding_ids and not findings:
        raise HTTPException(400, "findings is empty")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    from services.finding_scope import (
        drop_unverified_findings,
        reconcile_findings_against_stored_rows,
        resolve_findings_by_ids,
    )
    if finding_ids:
        # Part 2b F7 — the authoritative path: server loads the real
        # rows itself (effective account + project + verified + open)
        # instead of trusting whatever the client POSTed. Any id with
        # no matching row (foreign, unverified, already closed,
        # tampered) is silently dropped here, never fabricated.
        findings = await resolve_findings_by_ids(
            db, user_id=user_id, project_id=project_id, finding_ids=finding_ids,
        )
    else:
        # Legacy path — never trust client-supplied severity/title/
        # fix_hint for billing or LLM prompts; overwrite from the
        # stored row wherever one matches.
        findings = await reconcile_findings_against_stored_rows(
            db, user_id=user_id, project_id=project_id, findings=findings,
        )
    # Robot Fix List B2 — real-only gate applies to bulk-fix input
    # too, even when the caller POSTs findings directly instead of
    # going through the (already-filtered) fix-list endpoint.
    findings = await drop_unverified_findings(
        db, user_id=user_id, project_id=project_id, findings=findings,
    )
    if not findings:
        raise HTTPException(400, "findings is empty")
    if len(findings) > _BULK_MAX_FINDINGS:
        # Iter 212m-179 — HARD cap (was 500).  GitHub's secondary rate
        # limits 403 the pipeline mid-job past this point; failing fast
        # with a clear message beats a half-completed bulk run.
        raise HTTPException(400, {
            "error":     "bulk_limit_exceeded",
            "max":       _BULK_MAX_FINDINGS,
            "requested": len(findings),
            "message":   (f"Max {_BULK_MAX_FINDINGS} files per bulk run — "
                          "GitHub secondary rate-limit protection. Run the "
                          "rest as the next batch."),
        })

    # Iter 212m-190 — task-quota gate: tool access by tier, bulk (count
    # > 1) is Team-only, and N fixes need N remaining tasks. Raises
    # 403/402 with structured detail BEFORE any work starts. Tasks are
    # deducted per SUCCESSFUL fix inside the worker — failures never
    # burn quota. Quota is the (possibly shared) account's, via
    # `effective` — a team member's fix draws from the SAME monthly
    # task pool the owner and every other member share.
    tool = ((body or {}).get("tool") or "health-scan").strip()
    if tool not in ALL_FIX_TOOLS:
        tool = "health-scan"
    quota = await assert_can_fix(effective, tool, count=len(findings))
    is_unlim = quota["is_unlimited"]

    # Master Prompt Step C2 — Fix is locked behind a paid plan.
    # off=no-op (today's behavior, unchanged); shadow=log only;
    # on=typed 402 BEFORE any job is created. fix_entitlement is set
    # ONLY by the signature-verified Stripe webhook — never by this
    # endpoint or any frontend value.
    from services.fix_entitlement import assert_fix_entitled
    await assert_fix_entitled(db, user_id)

    job_id = await fjm.create_job(
        db=db, user_id=user_id, kind="bulk", total=len(findings),
        project_id=project_id, findings=findings,
    )

    # Master Prompt v2 Part 1.1 — N findings = N billable units. Hold
    # ALL of them up front, synchronously, before any work starts
    # (same "clean 402 before any spend" guarantee as every other fix
    # surface) — modeled as N independent per-finding holds rather
    # than one combined batch hold, so each finding settles/releases
    # through the SAME tested wallet.hold/settle/release primitives
    # used everywhere else, with no change to their semantics. Keyed
    # on a stable per-finding id (see _wallet_run_id) so a finding's
    # hold survives _run_bulk_job's own severity-based reordering.
    # `job_id` already exists at this point (fjm.create_job generates
    # it internally) — a request blocked here still closes that job
    # row as failed rather than leaving it stuck in "running" forever.
    from services.wallet import assert_wallet, finalize_hold
    held_run_ids: list[str] = []
    try:
        for finding in findings:
            run_id = _wallet_run_id(job_id, finding)
            await assert_wallet(db, user_id=user_id, action="fix", run_id=run_id,
                                idempotency_key=f"wallet_hold:{run_id}")
            held_run_ids.append(run_id)
    except HTTPException:
        for run_id in held_run_ids:
            await finalize_hold(db, user_id=user_id, action="fix", run_id=run_id,
                                ok=False, reason="bulk_hold_aborted")
        await fjm.close(db, job_id, ok=False, status="failed",
                        message="Insufficient tokens — no findings were attempted.")
        raise

    # Launch the worker — runs in the background, streams events.
    # `user=effective` — apply_finding_fix() reads the GitHub App
    # installation off the PROJECT, which belongs to the account
    # (owner), not to whichever team member happened to click Fix.
    asyncio.create_task(_run_bulk_job(
        job_id=job_id, db=db, user=effective, project_id=project_id,
        findings=findings, is_unlim=is_unlim, tool=tool,
    ))
    return {
        "ok":     True,
        "job_id": job_id,
        "count":  len(findings),
        "stream": f"/api/aurem-dev/fix-pipeline/stream/{job_id}",
    }


async def _run_bulk_job(*, job_id: str, db, user: dict, project_id: str,
                        findings: list[dict], is_unlim: bool,
                        tool: str = "health-scan") -> None:
    """The actual worker.  Findings are first interleaved by severity
    so each batch carries a mix of critical/high/medium/low fixes,
    then processed in chunks of _BULK_BATCH_SIZE.  Within a chunk we
    run sequentially (no Git conflicts on the same branch); between
    chunks we pause briefly so GitHub's branch indexer catches up.
    Every event is emitted onto the same job_id queue so the SSE
    drawer keeps streaming uninterrupted.

    Iter 212m-128 — Top-level try/except wrapper.  Any unhandled
    exception inside the worker (Mongo glitch, GitHub 5xx outside
    the per-finding block, programming bug) used to silently kill
    the asyncio task and leave the UI hanging in "running" forever.
    Now we catch, emit a `job-error` event with the full traceback,
    and close the job with status="failed" so the UI offers Restart.
    """
    user_id = user["user_id"]
    # Ticket D (2026-02) — per-fix before/after live proof, opt-in
    # (only when the project has a preview_url configured). Fire-and-
    # capture BEFORE anything is applied — this does NOT block or slow
    # the guardian per-file fast path (_guardian_rescan, untouched);
    # it's a single capture at the START of this already-async bulk
    # job, same cost class as the GitHub commit calls this job already
    # makes.
    #
    # Ticket D-UI (2026-02) — honest-state contract widened: a
    # "never attempted" project (no preview_url) still writes NOTHING
    # (the UI's empty state), but once `preview_url` IS set, this now
    # ALWAYS writes a `fix_before_after_proof` row — including total
    # failure — so the UI can show a real "could not verify" state
    # instead of silence. Desktop and mobile_375 are captured
    # independently (one device's capture failing never blocks the
    # other's).
    proof_attempted = False
    before_shots: dict[str, Optional[bytes]] = {"desktop": None, "mobile": None}
    before_fail: dict[str, Optional[str]] = {"desktop": None, "mobile": None}
    proj_doc = await db.cto_projects.find_one({"project_id": project_id}, {"_id": 0, "preview_url": 1})
    preview_url = (proj_doc or {}).get("preview_url")
    if preview_url:
        proof_attempted = True
        import services.deploy_verify as dv
        for _device, _vp in (("desktop", "desktop"), ("mobile", "mobile_375")):
            try:
                _res = await dv.capture_single_screenshot(
                    preview_url, db=db, user_id=user_id, project_id=project_id,
                    viewport=_vp)
                if _res.get("ok"):
                    before_shots[_device] = _res["screenshot"]
                else:
                    before_fail[_device] = _res.get("fail_reason") or "unknown"
                    logger.info("[fix-proof] before-capture (%s) not ok for job=%s: %s",
                                _device, job_id, before_fail[_device])
            except Exception as e:
                before_fail[_device] = f"capture_crashed:{type(e).__name__}"
                logger.warning("[fix-proof] before-capture (%s) crashed for job=%s: %r",
                               _device, job_id, e)
    try:
        ordered = _interleave_by_severity(findings)
        batches = [ordered[i : i + _BULK_BATCH_SIZE]
                   for i in range(0, len(ordered), _BULK_BATCH_SIZE)]
        # Iter 212m-147 — `fix_total` is the canonical count of fixes
        # the drawer's header shows ("Fixing N / fix_total"). Emit it
        # in job-start so the drawer can size its progress bar.
        fjm.emit(job_id, "job-start", count=len(ordered),
                 fix_total=len(ordered),
                 batches=len(batches), batch_size=_BULK_BATCH_SIZE,
                 is_unlimited=is_unlim, project_id=project_id)
        await fjm.persist_event(db, job_id)
        total_charged = 0
        total_ok = 0
        total_failed = 0
        total_skipped = 0
        # FIX_one_branch_for_all_fixes — every successful fix in this
        # job now shares the SAME branch/PR (services/fix_batches.py).
        # Captured from the first successful `res` so the final
        # summary can surface ONE link instead of the old N separate
        # per-fix PR links.
        shared_fix_branch_info: dict = {}
        # Iter 212m-147 — Final-summary roll-up.  Collected per-fix so
        # the `all-done` event can replay the whole journey for the
        # drawer's terminal summary card.
        fixes_summary: list[dict] = []

        # Part 2b F2 — real per-file fix. A whole-file pre-pass
        # resolves EVERY file exactly once via apply_file_fixes (one
        # LLM call + one commit per file, with its own retry loop at
        # FILE granularity), building a finding_id -> result lookup.
        # The existing per-finding loop below is UNCHANGED from this
        # point on — it now reads from this lookup instead of calling
        # apply_finding_fix, so every downstream behavior (SSE shape,
        # learning hooks, brain outcomes, wallet hold/settle, task-
        # quota) keeps working exactly as before, just fed from a
        # real per-FILE commit instead of N per-finding ones.
        file_groups: dict[str, list[dict]] = {}
        file_order: list[str] = []
        for f in ordered:
            fpath = f.get("file") or f.get("path") or ""
            if fpath not in file_groups:
                file_groups[fpath] = []
                file_order.append(fpath)
            file_groups[fpath].append(f)

        def _finding_lookup_key(f: dict) -> str:
            return f.get("id") or f.get("finding_id") or f.get("rule_id") or f.get("rule") or ""

        def _file_result_to_legacy_shape(file_res: dict, finding: dict) -> dict:
            """Reshape one entry of apply_file_fixes's `results` list
            (plus the whole-file result it came from) into the exact
            dict shape apply_finding_fix used to return, so every
            caller below this point needs zero changes."""
            rid = finding.get("rule_id") or finding.get("rule") or ""
            path = finding.get("file") or finding.get("path") or ""
            key = _finding_lookup_key(finding)
            match = None
            for r in (file_res.get("results") or []):
                if r.get("finding_id") == key or (rid and r.get("rule_id") == rid):
                    match = r
                    break
            if match is None:
                # The whole-file call itself never got far enough to
                # produce per-finding results (batch_unavailable,
                # github_push_failed, missing_required_args, ...) —
                # every finding in the group shares that ONE reason.
                return {"ok": False, "error": file_res.get("error") or "unknown",
                        "file": path, "rule_id": rid}
            status = match.get("status")
            if status == "resolved":
                return {
                    "ok": True, "file": path, "rule_id": rid,
                    "commit_sha": file_res.get("commit_sha"),
                    "full_sha": file_res.get("full_sha"),
                    "html_url": file_res.get("html_url"),
                    "pr_url": file_res.get("pr_url"),
                    "branch": file_res.get("branch"),
                    "batch_id": file_res.get("batch_id"),
                    "original_content": file_res.get("original_content"),
                    "patched_content": file_res.get("patched_content"),
                    # Part 2b R6 — needed by the frontend's "Undo this
                    # fix" button.
                    "fix_id": match.get("fix_id"),
                }
            if status == "skipped_not_present":
                return {
                    "ok": True, "skipped_not_present": True,
                    "file": path, "rule_id": rid,
                    "branch": file_res.get("branch"), "pr_url": file_res.get("pr_url"),
                    "batch_id": file_res.get("batch_id"),
                }
            if status == "remaining":
                return {"ok": False, "error": "patch_did_not_resolve_finding",
                        "file": path, "rule_id": rid}
            # status == "error"
            err = match.get("error") or "unknown"
            return {"ok": False,
                    "error": "llm_made_no_change" if err == "llm_made_no_change" else err,
                    "file": path, "rule_id": rid,
                    "retryable": err == "llm_made_no_change"}

        _file_fixes_cache: dict[str, dict] = {}

        async def _resolve_file_once(fpath: str) -> dict:
            if fpath in _file_fixes_cache:
                return _file_fixes_cache[fpath]
            group = file_groups[fpath]
            last_err = None
            file_res: dict = {"ok": False, "error": "unknown", "results": []}
            for attempt in range(1, _MAX_FIX_ATTEMPTS + 1):
                try:
                    file_res = await apply_file_fixes(
                        db=db, user=user, project_id=project_id,
                        file=fpath, findings=group,
                    )
                except Exception as e:                           # noqa: BLE001
                    logger.exception("apply_file_fixes raised for file=%s attempt=%d",
                                     fpath, attempt)
                    file_res = {"ok": False, "error": f"unhandled: {e}", "results": []}
                if file_res.get("ok"):
                    break
                last_err = file_res.get("error") or "unknown"
                if last_err in _TERMINAL_ERROR_CODES or attempt >= _MAX_FIX_ATTEMPTS:
                    break
                backoff = _RETRY_BACKOFFS_S[min(attempt - 1, len(_RETRY_BACKOFFS_S) - 1)]
                fjm.emit(job_id, "retrying", file=fpath, attempt=attempt + 1,
                         of=_MAX_FIX_ATTEMPTS, last_error=last_err, backoff_s=backoff)
                await asyncio.sleep(backoff)
            _file_fixes_cache[fpath] = file_res
            return file_res

        global_idx = 0
        for batch_no, batch in enumerate(batches, start=1):
            fjm.emit(job_id, "batch-start",
                     batch=batch_no, of=len(batches), size=len(batch),
                     severities=[f.get("severity") or "" for f in batch])
            for finding in batch:
                global_idx += 1
                # Iter 212m-178 — pace GitHub mutations. Firing
                # blob+tree+commit+ref for one fix and immediately
                # reading the next file tripped GitHub's SECONDARY rate
                # limit (bulk 2nd+ fix failed with github_status_403).
                # A short gap between findings keeps us under the burst
                # threshold; the read-side Retry-After handler covers
                # the rest.
                if global_idx > 1:
                    await asyncio.sleep(_BULK_INTER_FIX_DELAY_S)
                finding_id = (finding.get("id") or finding.get("rule_id")
                              or f"f_{global_idx}")
                rule_id    = finding.get("rule_id") or finding.get("rule") or ""
                path       = finding.get("file") or finding.get("path") or ""
                severity   = finding.get("severity") or ""

                fjm.emit(job_id, "queued",
                         index=global_idx, batch=batch_no,
                         finding_id=finding_id, rule_id=rule_id,
                         severity=severity, file=path,
                         # Iter 212m-147 — fix_index/fix_total + a
                         # human description so the drawer header
                         # ("Fixing 3/15") and active-fix block
                         # ("Reading file…") have all data on event 1.
                         fix_index=global_idx, fix_total=len(ordered),
                         description=(finding.get("title")
                                      or finding.get("message")
                                      or rule_id))

                # Iter 212m-190 — no pre-deduction. 1 task is recorded
                # per SUCCESSFUL fix (below), so failed fixes never
                # burn quota. Quota was asserted before job start.

                fjm.emit(job_id, "reading", finding_id=finding_id,
                         file=path,
                         fix_index=global_idx, fix_total=len(ordered))
                # Iter 212m-128 — Per-finding auto-retry.  We loop up to
                # _MAX_FIX_ATTEMPTS times, bailing early on terminal
                # error codes (see _TERMINAL_ERROR_CODES) where retrying
                # is pointless.  Each retry emits a `retrying` SSE event
                # so the drawer renders the attempt counter and the UI
                # never feels stuck on a hung step.
                res = None
                last_err = None
                attempts_used = 0
                _t_fix_start = time.time()
                # ORA Brain Part 4, F2 — tag this finding's own fix
                # attempt so the LLM call inside apply_finding_fix
                # stamps project_id + a per-finding task_id onto its
                # brain_calls row (services/brain_gateway.py), letting
                # the outcome hook below find it via
                # attach_outcome_for_task. Uses llm_surface_context
                # (properly scoped + reset), NOT tag_request_surface's
                # set-and-forget — this is a sequential per-finding
                # loop sharing ONE request Task across many findings,
                # exactly the case tag_request_surface's own docstring
                # says not to use it for.
                from services.loop_token_ledger import llm_surface_context
                async with llm_surface_context(
                    project_id=project_id, task_id=f"fix:{finding_id}",
                ):
                    # Part 2b F2 — the real per-file fix: resolve this
                    # finding's FILE exactly once (cached across every
                    # other finding in the same file-group — one LLM
                    # call, one commit, retried at FILE granularity
                    # inside _resolve_file_once), then reshape that
                    # file-level result into this finding's own `res`,
                    # so every line below is unchanged.
                    try:
                        file_res = await _resolve_file_once(path)
                        attempts_used = 1
                    except Exception as e:                            # noqa: BLE001
                        logger.exception("apply_file_fixes raised for finding=%s "
                                         "file=%s", finding_id, path)
                        file_res = {"ok": False, "error": f"unhandled: {e}", "results": []}
                        attempts_used = 1
                    res = _file_result_to_legacy_shape(file_res, finding)
                    last_err = res.get("error") if not res.get("ok") else None
                _fix_duration_ms = int((time.time() - _t_fix_start) * 1000)

                # Master Prompt v2 Part 1.1 — settle this finding's own
                # hold on success, release it on failure. Each finding
                # is its own billable unit; one finding's outcome never
                # affects another's hold.
                from services.wallet import finalize_hold as _finalize_wallet_hold
                # FIX_one_branch_for_all_fixes, design item 12 — an
                # idempotent skip (file already has this exact fix on
                # the shared branch) is not charged, same rule as a
                # real failure.
                await _finalize_wallet_hold(
                    db, user_id=user_id, action="fix",
                    run_id=_wallet_run_id(job_id, finding),
                    ok=bool(res.get("ok")) and not res.get("skipped_no_change")
                       and not res.get("skipped_not_present"),
                    reason=last_err or "",
                )

                if not res.get("ok"):
                    fjm.emit(job_id, "fix-done",
                             ok=False, finding_id=finding_id,
                             error=res.get("error") or "unknown",
                             attempts=_MAX_FIX_ATTEMPTS,
                             file=path, rule_id=rule_id,
                             fix_index=global_idx,
                             fix_total=len(ordered))
                    await fjm.persist_event(db, job_id)
                    total_failed += 1
                    fixes_summary.append({
                        "index":      global_idx,
                        "file":       path,
                        "rule":       rule_id,
                        "status":     "failed",
                        "error":      res.get("error") or "unknown",
                        "commit_sha": None,
                        "github_url": None,
                    })
                    # Iter 212m-129 — Learning hook (failure path).
                    await ora_fix_learning.record_fix_outcome(
                        db, user_id=user_id, project_id=project_id,
                        finding=finding, result=res,
                        attempts=attempts_used,
                        duration_ms=_fix_duration_ms,
                        tokens_charged=0,   # never deducted (task-quota model)
                        scanner=finding.get("scanner"),
                    )
                    # ORA Brain Part 4, F2 — objective outcome signal.
                    # Best-effort: never blocks or fails the user-
                    # visible response.
                    try:
                        from services.brain_gateway import attach_outcome_for_task
                        await attach_outcome_for_task(
                            db, task_id=f"fix:{finding_id}",
                            outcome="tests_failed", user_id=user_id,
                        )
                    except Exception as _bge:                        # noqa: BLE001
                        logger.debug("brain outcome attach skipped: %r", _bge)
                    continue

                if res.get("skipped_no_change") or res.get("skipped_not_present"):
                    # Part 2b R1 — a skip is neither a real fix nor a
                    # failure; count it separately so the bulk summary
                    # never claims N findings were "fixed" when some
                    # were only skipped (already-fixed / no-longer-
                    # present). No commit happened, so no diff/verify.
                    fjm.emit(job_id, "fix-done",
                             ok=True, finding_id=finding_id,
                             project_id=project_id,
                             skipped_no_change=bool(res.get("skipped_no_change")),
                             skipped_not_present=bool(res.get("skipped_not_present")),
                             file=path, rule_id=rule_id,
                             fix_index=global_idx, fix_total=len(ordered))
                    await fjm.persist_event(db, job_id)
                    total_skipped += 1
                    fixes_summary.append({
                        "index": global_idx, "file": path, "rule": rule_id,
                        "status": "skipped_not_present" if res.get("skipped_not_present")
                                  else "skipped_no_change",
                        "commit_sha": None, "github_url": None,
                    })
                    continue

                # Iter 212m-147 — Emit the diff BEFORE the GitHub
                # verification call so the drawer animates `+`/`-`
                # lines in immediately after the LLM patch lands,
                # then transitions to the "Committing" badge while
                # the verify call runs.  Diff is computed from the
                # before/after contents apply_finding_fix returned.
                _diff_lines = _compute_diff_lines(
                    res.get("original_content") or "",
                    res.get("patched_content")  or "",
                )
                if _diff_lines:
                    fjm.emit(job_id, "fix-diff",
                             finding_id=finding_id,
                             fix_index=global_idx,
                             fix_total=len(ordered),
                             file=path, rule_id=rule_id,
                             diff=_diff_lines)
                fjm.emit(job_id, "fix-committing",
                         finding_id=finding_id,
                         fix_index=global_idx,
                         fix_total=len(ordered),
                         file=path, rule_id=rule_id,
                         commit_sha=res.get("commit_sha"))

                # Real GitHub verification — confirm the commit exists.
                fjm.emit(job_id, "verifying",
                         finding_id=finding_id,
                         commit_sha=res.get("commit_sha"),
                         fix_index=global_idx,
                         fix_total=len(ordered))
                verified = await _verify_commit_exists(
                    db=db, user=user, project_id=project_id,
                    full_sha=res.get("full_sha"),
                )
                # 2026-10 — Guardian re-scan: run the full category
                # scanner against the final patched file content so
                # the drawer can show "verified clean" / "N other
                # issues remain" instead of going silent after a fix.
                guardian = _guardian_rescan(
                    finding.get("category") or "",
                    res.get("file") or path,
                    res.get("patched_content") or "",
                )
                fjm.emit(job_id, "fix-done",
                         ok=True, finding_id=finding_id,
                         # Robot Fix List B6/F2 — the frontend's
                         # `aurem:finding-fixed` window event is built
                         # straight from this payload; it needs
                         # project_id so a listener can ignore a fix
                         # that belongs to a DIFFERENT project (F3).
                         project_id=project_id,
                         commit_sha=res.get("commit_sha"),
                         full_sha=res.get("full_sha"),
                         html_url=res.get("html_url"),
                         github_url=res.get("html_url"),
                         pr_url=res.get("pr_url"),
                         branch=res.get("branch"),
                         file=res.get("file"),
                         rule_id=res.get("rule_id"),
                         # Part 2b R6 — needed by the frontend's
                         # "Undo this fix" button.
                         fix_id=res.get("fix_id"),
                         fix_index=global_idx,
                         fix_total=len(ordered),
                         # 2026-08-23 — tri-state, not a bool: True=
                         # confirmed on GitHub, False=confirmed absent
                         # (rare, real problem), None=check itself was
                         # inconclusive (rate limit/network) — the
                         # commit may well be fine, just unconfirmed.
                         # The drawer must show a "Check now" retry for
                         # None, not silently treat it like False.
                         verified=verified,
                         guardian_rescan=guardian)
                await fjm.persist_event(db, job_id)
                fixes_summary.append({
                    "index":      global_idx,
                    "file":       res.get("file"),
                    "rule":       res.get("rule_id"),
                    "status":     "ok",
                    "commit_sha": res.get("commit_sha"),
                    "github_url": res.get("html_url"),
                    "pr_url":     res.get("pr_url"),
                    "verified":   verified,
                    "fix_id":     res.get("fix_id"),
                })
                # Iter 212m-129 — Learning hook (success path).  The
                # `verified` field carries the real GitHub-commit-
                # exists check so analytics can distinguish "LLM said
                # it fixed it" from "GitHub actually has the commit".
                await ora_fix_learning.record_fix_outcome(
                    db, user_id=user_id, project_id=project_id,
                    finding=finding,
                    result={**res, "verified": verified},
                    attempts=attempts_used,
                    duration_ms=_fix_duration_ms,
                    tokens_charged=0,
                    scanner=finding.get("scanner"),
                )
                # ORA Brain Part 4, F2 — objective outcome signal. Only
                # the real GitHub-commit-verified case counts as a
                # strong positive signal; "ok but not (yet) verified"
                # deliberately attaches nothing rather than inventing
                # a confidence level record_fix_outcome doesn't claim.
                if verified:
                    try:
                        from services.brain_gateway import attach_outcome_for_task
                        await attach_outcome_for_task(
                            db, task_id=f"fix:{finding_id}",
                            outcome="verification_passed", user_id=user_id,
                        )
                    except Exception as _bge:                        # noqa: BLE001
                        logger.debug("brain outcome attach skipped: %r", _bge)
                if res.get("pr_url") and not shared_fix_branch_info:
                    shared_fix_branch_info = {
                        "pr_url": res.get("pr_url"), "branch": res.get("branch"),
                        "batch_id": res.get("batch_id"),
                    }
                total_ok += 1
                # Iter 212m-193 — persist the fixed state so a rescan
                # doesn't resurrect this finding (fix lives on a draft
                # PR branch; base branch is unchanged until merge).
                try:
                    await record_fixed(
                        db, user_id=user_id, project_id=project_id,
                        finding=finding,
                        commit_sha=res.get("commit_sha") or "",
                        html_url=res.get("html_url") or "",
                        tool=tool,
                    )
                except Exception:
                    pass
                # Iter 212m-190 — deduct exactly 1 task PER successful
                # fix (atomic $inc). Failed fixes above never reach
                # this line, so a 12-selected / 2-failed run deducts 10.
                # FIX_one_branch_for_all_fixes, design item 12 — an
                # idempotent skipped_no_change re-run is also excluded.
                # Part 2b R1 — skipped_not_present (finding already
                # gone before the LLM ever ran) is excluded the same way.
                if not is_unlim and not res.get("skipped_no_change") \
                        and not res.get("skipped_not_present"):
                    try:
                        await record_scan_fixes(user_id, tool, 1)
                        total_charged += 1
                    except Exception as _e:
                        logger.warning("task record failed user=%s: %r",
                                       user_id, _e)

            # End of batch — emit a summary event the UI uses for the
            # progress-bar tick + take a short breather before the next
            # batch so GitHub's branch indexer catches up.
            fjm.emit(job_id, "batch-end",
                     batch=batch_no, of=len(batches),
                     fixed_so_far=total_ok)
            await fjm.persist_event(db, job_id)
            if batch_no < len(batches):
                await asyncio.sleep(_INTER_BATCH_BREATHE_S)

        # Ticket D-UI (2026-02) — AFTER capture + diff, per device.
        # Once preview_url is configured (proof_attempted=True), this
        # ALWAYS writes a fix_before_after_proof row — including total
        # failure — so the UI has a real "could not verify" state
        # instead of silence. A device only gets images if BOTH its
        # before and after captures succeeded (never a half proof).
        if proof_attempted:
            try:
                from services.preview_capture import upload_receipt
                device_proof = {}
                for _device, _vp in (("desktop", "desktop"), ("mobile", "mobile_375")):
                    entry = {
                        "before_ok": before_shots[_device] is not None,
                        "before_fail_reason": before_fail[_device],
                        "after_ok": False, "after_fail_reason": None,
                        "changed": None, "diff_percent": None,
                        "before_key": None, "after_key": None, "diff_key": None,
                    }
                    if before_shots[_device] is not None:
                        after_result = await dv.capture_single_screenshot(
                            preview_url, db=db, user_id=user_id,
                            project_id=project_id, viewport=_vp)
                        after_bytes = after_result["screenshot"] if after_result.get("ok") else None
                        entry["after_ok"] = after_bytes is not None
                        entry["after_fail_reason"] = after_result.get("fail_reason")
                        if after_bytes is not None:
                            diff = dv.diff_screenshots(before_shots[_device], after_bytes)
                            entry["changed"] = diff.get("changed")
                            entry["diff_percent"] = diff.get("diff_percent")
                            entry["before_key"] = await upload_receipt(
                                before_shots[_device], f"fix-proof/{job_id}-{_device}-before.jpg")
                            entry["after_key"] = await upload_receipt(
                                after_bytes, f"fix-proof/{job_id}-{_device}-after.jpg")
                            if diff.get("diff_image"):
                                entry["diff_key"] = await upload_receipt(
                                    diff["diff_image"], f"fix-proof/{job_id}-{_device}-diff.jpg")
                    device_proof[_device] = entry
                await db.fix_before_after_proof.update_one(
                    {"_id": job_id},
                    {"$set": {
                        "job_id": job_id, "user_id": user_id, "project_id": project_id,
                        "attempted": True, "devices": device_proof,
                        "created_at": time.time(),
                    }},
                    upsert=True,
                )
                fjm.emit(job_id, "fix-proof-ready", job_id=job_id,
                         changed=device_proof["desktop"]["changed"],
                         diff_percent=device_proof["desktop"]["diff_percent"],
                         changed_mobile=device_proof["mobile"]["changed"],
                         diff_percent_mobile=device_proof["mobile"]["diff_percent"])
            except Exception as e:
                logger.warning("[fix-proof] after-capture/diff crashed for job=%s: %r", job_id, e)
                try:
                    await db.fix_before_after_proof.update_one(
                        {"_id": job_id},
                        {"$set": {"job_id": job_id, "user_id": user_id,
                                  "project_id": project_id, "attempted": True,
                                  "devices": {}, "crashed": True,
                                  "created_at": time.time()}},
                        upsert=True,
                    )
                except Exception:
                    pass

        if shared_fix_branch_info:
            # FIX_one_branch_for_all_fixes, design item 5 — ONE link
            # for the whole job, not N per-fix links.
            fjm.emit(job_id, "batch-info", **shared_fix_branch_info,
                     fix_count=total_ok)
            await fjm.persist_event(db, job_id)
        await fjm.close(
            db, job_id, ok=True,
            message=f"Fixed {total_ok}/{len(ordered)} "
                    + (f" · {total_skipped} already resolved" if total_skipped else "")
                    + f" ({total_charged} tasks used · "
                    f"{len(batches)} batches of {_BULK_BATCH_SIZE})",
        )
        # P2-A (2026-08-28) — user-facing bell, "ship_done".
        await emit_notification(
            db, user_id=user_id, type="ship_done",
            text=f"Fixed and shipped {total_ok}/{len(ordered)} findings.")
    except asyncio.CancelledError:
        # Task was cancelled by the runtime (graceful shutdown).
        # Mark as orphaned so the UI offers restart on next page load.
        logger.warning("bulk job cancelled mid-flight job=%s", job_id)
        await _release_all_bulk_holds(db, user_id, job_id, findings)
        try:
            fjm.emit(job_id, "job-error", ok=False,
                     reason="cancelled",
                     message="Worker cancelled — pod may be restarting.")
        except Exception:
            pass
        await fjm.close(
            db, job_id, ok=False, status="orphaned",
            message="Worker cancelled mid-flight. Click Restart to resume.",
        )
        raise
    except Exception as e:                                  # noqa: BLE001
        # Top-level catch: a Mongo glitch, an upstream GitHub 5xx that
        # escaped the per-finding block, a programming bug — instead
        # of silently dying and leaving "running" forever, we emit a
        # final `job-error` SSE event and close the job as `failed`
        # so the drawer can show a real error message + a Restart
        # button driving POST /restart/{job_id}.
        import traceback
        tb = traceback.format_exc(limit=4)
        logger.exception("bulk job crashed job=%s", job_id)
        await _release_all_bulk_holds(db, user_id, job_id, findings)
        try:
            fjm.emit(job_id, "job-error", ok=False,
                     reason="worker_exception",
                     error=f"{type(e).__name__}: {e}",
                     traceback=tb,
                     message="Worker crashed — click Restart to retry the remaining findings.")
        except Exception:
            pass
        await fjm.close(
            db, job_id, ok=False, status="failed",
            message=f"Worker crashed: {type(e).__name__}: {str(e)[:200]}",
        )
        # P2-A (2026-08-28) — user-facing bell, "ship_failed" (persistent).
        await emit_notification(
            db, user_id=user_id, type="ship_failed",
            text="A fix job crashed and needs your attention — click Restart to retry.")


def _wallet_run_id(job_id: str, finding: dict) -> str:
    """Master Prompt v2 Part 1.1 — a stable per-finding wallet hold key
    that survives `_interleave_by_severity`'s reordering. Must give the
    SAME value for the same finding whether computed over the original
    `findings` list (start_bulk_fix, before reordering) or the
    `ordered` list (_run_bulk_job, after reordering) — so it can't be
    positional. Falls back to file:line (stable identity within one
    batch) only when a finding has neither id nor rule_id."""
    key = finding.get("id") or finding.get("rule_id") or \
        f"{finding.get('file')}:{finding.get('line')}"
    return f"{job_id}:{key}"


async def _release_all_bulk_holds(db, user_id: str, job_id: str,
                                  findings: list[dict]) -> None:
    """Master Prompt v2 Part 1.1 — cleanup for a bulk job that crashed
    or was cancelled mid-flight: release every finding's hold,
    regardless of whether it was already settled/released inline
    (release() on an already-resolved run_id is a documented safe
    no-op — see services/wallet.py), so no finding the loop never
    reached (or was mid-attempt on) leaks a stuck hold."""
    from services.wallet import finalize_hold
    for finding in findings:
        try:
            await finalize_hold(
                db, user_id=user_id, action="fix",
                run_id=_wallet_run_id(job_id, finding),
                ok=False, reason="bulk_job_crashed_or_cancelled",
            )
        except Exception as e:                                # noqa: BLE001
            logger.warning(
                "[wallet] bulk cleanup release failed job=%s finding=%s: %r",
                job_id, finding.get("id") or finding.get("rule_id"), e,
            )


def _interleave_by_severity(findings: list[dict]) -> list[dict]:
    """Sort findings into severity buckets (critical→high→medium→low,
    unknown trailing) and round-robin them so the output contains a
    mix of severities at every position.  Order within a bucket is
    preserved so the original scanner ordering still wins ties.

    Example with [C, C, C, H, H, M, M, L] →
       [C, H, M, L, C, H, M, C].  Each batch of 10 will therefore
    contain at least one critical AND at least one high/medium/low
    whenever the source list has them, giving the founder a visible
    mix of fixes shipping per batch instead of 'critical first'.
    """
    buckets: dict[str, list[dict]] = {k: [] for k in _SEVERITY_BUCKET_ORDER}
    trailing: list[dict] = []
    for f in findings:
        sev = (f.get("severity") or "").lower()
        if sev in buckets:
            buckets[sev].append(f)
        else:
            trailing.append(f)

    out: list[dict] = []
    while any(buckets[k] for k in _SEVERITY_BUCKET_ORDER):
        for k in _SEVERITY_BUCKET_ORDER:
            if buckets[k]:
                out.append(buckets[k].pop(0))
    out.extend(trailing)
    return out


def _guardian_rescan(category: str, path: str, patched_content: str) -> Optional[dict]:
    """2026-10 — "Guardian re-scan" (founder spec, roadmap.sh "safe fix
    loop" criterion: verify the fix actually worked, not just that the
    LLM said so). `apply_finding_fix()` already re-validates the ONE
    specific `rule_id` that triggered this fix before committing
    (`_finding_still_present`, services/finding_fix_applier.py) — that
    check is narrow by design (one rule, pre-commit). This runs the
    FULL category scanner (every rule in that category, not just the
    one that was fixed) against the final committed file content, so
    the user gets an honest "this file is now clean for this category"
    / "N other issues of this type remain in this file" signal instead
    of silence after a fix lands. Best-effort: unknown category or a
    scanner exception must never fail the fix itself — the commit
    already succeeded and was verified independently.

    2026-10 (live-tested fix) — 4 categories look at the WHOLE repo's
    file list, not one file (git_hygiene/documentation/seo_basics check
    "does README.md/.env.example/robots.txt exist anywhere"; test_coverage
    checks "is there a test file anywhere"). Re-running them against a
    single-file `{path: content}` dict always reports every sibling file
    as missing — real end-to-end test confirmed this: fixing a missing
    `.gitignore` came back "1 other issue remains" (actually just
    "README.md isn't in this 1-file dict", unrelated noise). Skip the
    re-scan for those 4 — a single file can never give an honest signal
    for a repo-wide check, and a fabricated "issue remains" is worse
    than no signal at all.
    """
    _REPO_LEVEL_CATEGORIES = {"git_hygiene", "documentation", "seo_basics", "test_coverage"}
    if category in _REPO_LEVEL_CATEGORIES:
        return None
    try:
        from services.codebase_health_core import SCANNERS
        scanner = SCANNERS.get(category)
        if not scanner or not path:
            return None
        remaining = scanner({path: patched_content or ""})
        return {"category": category, "clean": len(remaining) == 0,
                "remaining_in_file": len(remaining)}
    except Exception:                                      # noqa: BLE001
        return None



async def _verify_commit_exists(*, db, user: dict, project_id: str,
                                full_sha: Optional[str]) -> Optional[bool]:
    """Hit GET /repos/{owner}/{repo}/commits/{sha} to confirm the
    commit landed on GitHub.  No `True` shortcut without a real
    HTTP 200 + non-empty parents list — the user explicitly asked
    for proof, not optimism.

    2026-08-23 — returns `None` (not `False`) when the CHECK ITSELF
    is inconclusive (network error, rate limit, timeout) rather than
    when the commit is confirmed absent. Callers must not treat these
    the same: `False` is a real "commit missing" signal, `None` means
    "try the check again," never "the fix failed."
    """
    if not full_sha:
        return False
    try:
        proj = await db.cto_projects.find_one(
            {"project_id": project_id, "user_id": user["user_id"]},
            {"_id": 0, "github_owner": 1, "github_repo": 1, "github_token": 1,
             "auth_method": 1, "installation_id": 1, "user_id": 1},
        )
        if not proj:
            return None
        owner = proj.get("github_owner")
        repo  = proj.get("github_repo")
        # 2026-02-11 · Phase 3b (Bug 2 fix) — get_repo_token dispatches on
        # project.auth_method so App-installed projects also succeed.
        # 2026-06 PAT-removal — App-only, no OAuth fallback.
        from services.pat_vault import get_repo_token_or_error
        token, _auth_err, _ = await get_repo_token_or_error(proj)
        if not (owner and repo and token):
            return None
        import httpx

        from services.http import ext_client
        async with ext_client("github", timeout=httpx.Timeout(10.0)) as cx:
            r = await cx.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{full_sha}",
                headers={"Authorization": f"token {token}",
                         "Accept": "application/vnd.github+json",
                         "User-Agent": "aurem-fix-verifier"},
            )
            if r.status_code == 404:
                return False  # genuinely confirmed absent — rare, real
            if r.status_code != 200:
                return None   # rate limit / 5xx / other — inconclusive, retry-able
            data = r.json() or {}
            # A real commit always carries an `html_url` and a `sha`.
            return bool(data.get("sha") == full_sha and data.get("html_url"))
    except Exception as e:                                    # noqa: BLE001
        logger.warning("verify_commit_exists soft-failed (inconclusive): %r", e)
        return None


# ─── SSE stream ────────────────────────────────────────────────────────
@router.get("/stream/{job_id}")
async def stream_fix(job_id: str,
                     token: Optional[str] = None,
                     authorization: Optional[str] = Header(None)):
    """Server-Sent Events.  Re-auth via the standard JWT; rejects
    cross-tenant subscriptions so one user can't tail another's
    fix job.

    Auth fallback: browsers cannot set Authorization headers on an
    EventSource, so we also accept `?token=...` in the query string.
    Same JWT, same validation path — just a different transport.

    Iter 212m-128 — Mongo hydration. If the job is no longer in
    memory (pod restart, multi-pod miss) but its row exists in
    `fix_jobs`, we replay its terminal snapshot as a `hydrated`
    event so the drawer can render the partial results AND a
    Restart button instead of a blank "gone" screen.
    """
    if not authorization and token:
        authorization = f"Bearer {token}"
    user, effective = await _gate(authorization, "read")
    db = get_db()
    summary = fjm.get_summary(job_id)

    # If not in memory, try Mongo for owner-check before letting the
    # generator stream the hydrated payload.
    if not summary:
        persisted = await fjm.get_persisted(db, job_id, effective["user_id"])
        if persisted is None and not user.get("is_admin"):
            # Don't 403 — the user might be the legitimate owner but
            # we lost the row entirely.  Let the generator emit
            # `gone` so the drawer can render "expired".
            async def _empty():
                yield {"event": "phase",
                       "data": json.dumps({
                           "phase":   "gone",
                           "ts":      time.time(),
                           "message": "Job not found (may have expired)",
                           "can_restart": False,
                       })}
            return EventSourceResponse(_empty())
    else:
        # Owner check — in-memory job must belong to the caller's
        # (team-resolved) account.
        job = fjm._JOBS.get(job_id) or {}                 # noqa: SLF001
        # Part 2b R5 — fail CLOSED: a job with no recorded owner is
        # denied to everyone, never implicitly granted.
        if job.get("user_id") != effective["user_id"] \
                and not user.get("is_admin"):
            raise HTTPException(404, "Job not found")

    async def _event_stream():
        async for ev in fjm.subscribe(job_id, db=db):
            yield {"event": "phase", "data": json.dumps(ev)}

    return EventSourceResponse(_event_stream())


# ─── Polling fallback ──────────────────────────────────────────────────
@router.get("/summary/{job_id}")
async def get_job_summary(job_id: str,
                           authorization: Optional[str] = Header(None)) -> dict:
    user, effective = await _gate(authorization, "read")
    db = get_db()
    s = fjm.get_summary(job_id)
    if not s:
        # Fall back to Mongo so a multi-pod / post-restart caller
        # still gets a polling response.
        persisted = await fjm.get_persisted(db, job_id, effective["user_id"])
        if not persisted:
            raise HTTPException(404, "Job not found")
        return {"ok": True, "from_mongo": True, **persisted}
    job = fjm._JOBS.get(job_id) or {}                         # noqa: SLF001
    # Part 2b R5 — fail CLOSED (see the identical note above).
    if job.get("user_id") != effective["user_id"] \
            and not user.get("is_admin"):
        raise HTTPException(404, "Job not found")
    return {"ok": True, **s}


# ─── Ticket D (2026-02) — per-fix before/after live proof ─────────────
@router.get("/proof/{job_id}/meta")
async def get_fix_before_after_proof_meta(job_id: str,
                                           authorization: Optional[str] = Header(None)) -> dict:
    """Ticket D-UI (2026-02) — a single cheap JSON call the drawer uses
    to decide which state to render (loading/empty/could-not-verify/
    success) BEFORE fetching any image bytes. Honest states:
      - no doc at all           -> {attempted: False}   (never tried —
                                     empty state, no preview_url configured)
      - doc with attempted=True -> per-device {before_ok, after_ok,
                                     changed, diff_percent, fail reasons}
                                     — a device only has images when
                                     both before_ok and after_ok."""
    user, effective = await _gate(authorization, "read")
    db = get_db()
    doc = await db.fix_before_after_proof.find_one({"_id": job_id}, {"_id": 0})
    if not doc or (doc.get("user_id") != effective["user_id"] and not user.get("is_admin")):
        return {"ok": True, "attempted": False}
    devices = doc.get("devices") or {}
    out = {}
    for device in ("desktop", "mobile"):
        d = devices.get(device) or {}
        out[device] = {
            "before_ok": bool(d.get("before_ok")),
            "after_ok": bool(d.get("after_ok")),
            "has_images": bool(d.get("before_key") and d.get("after_key")),
            "before_fail_reason": d.get("before_fail_reason"),
            "after_fail_reason": d.get("after_fail_reason"),
            "changed": d.get("changed"),
            "diff_percent": d.get("diff_percent"),
        }
    return {"ok": True, "attempted": True, "crashed": bool(doc.get("crashed")),
            "devices": out}


@router.get("/proof/{job_id}")
async def get_fix_before_after_proof(job_id: str,
                                      variant: str = "before",
                                      device: str = "desktop",
                                      authorization: Optional[str] = Header(None)):
    """Streams back one of the 3 receipt images (before/after/diff) for
    one device (desktop/mobile), same ownership check as /summary.
    Honest 404 when this job had no preview_url (no proof attempted)
    or that device's capture failed/degraded (no key stored)."""
    from fastapi import Response as _Response
    user, effective = await _gate(authorization, "read")
    db = get_db()
    doc = await db.fix_before_after_proof.find_one({"_id": job_id}, {"_id": 0})
    if not doc or doc.get("user_id") != effective["user_id"] and not user.get("is_admin"):
        raise HTTPException(404, "No before/after proof for this job")
    device_entry = (doc.get("devices") or {}).get(device) or {}
    key = {"before": device_entry.get("before_key"), "after": device_entry.get("after_key"),
           "diff": device_entry.get("diff_key")}.get(variant)
    if not key:
        raise HTTPException(404, f"No {variant} capture stored for this job/device")
    from services.preview_capture import fetch_receipt
    data = await fetch_receipt(key)
    if not data:
        raise HTTPException(404, "Receipt data not found in storage")
    return _Response(content=data, media_type="image/jpeg")


# ─── 2026-08-23 — manual "Check now" retry for inconclusive verify ────
@router.get("/verify-commit")
async def retry_verify_commit(job_id: str, finding_id: str,
                               authorization: Optional[str] = Header(None)) -> dict:
    """The commit-exists check can itself fail (rate limit, network)
    independent of whether the fix succeeded. This lets the UI ask
    "check again" without re-running the whole fix."""
    user, effective = await _gate(authorization, "read")
    db = get_db()
    s = fjm.get_summary(job_id) or await fjm.get_persisted(db, job_id, effective["user_id"])
    if not s:
        raise HTTPException(404, "Job not found")
    # Part 2b R5 — fail CLOSED (see the identical note above).
    if s.get("user_id") != effective["user_id"] and not user.get("is_admin"):
        raise HTTPException(404, "Job not found")
    project_id = s.get("project_id")
    row = next((r for r in (s.get("results") or [])
                if r.get("finding_id") == finding_id and r.get("ok")), None)
    if not row:
        raise HTTPException(404, "Finding not found in this job")
    verified = await _verify_commit_exists(
        db=db, user=effective, project_id=project_id, full_sha=row.get("full_sha"),
    )
    await fjm.update_result_verified(db, job_id, finding_id, verified)
    return {"ok": True, "finding_id": finding_id, "verified": verified}


# ─── Iter 212m-128 — List user's recent jobs ──────────────────────────
@router.get("/list")
async def list_user_jobs(
    status: Optional[str] = None,
    limit: int = 20,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Returns the account's recent fix jobs (newest first) — shared
    across every team member with "loop" access, not just whoever
    happened to click the button. Used by the Dashboard "Resume
    in-flight fix" banner + the FixProgressDrawer re-attach logic on
    page reload."""
    _, effective = await _gate(authorization, "read")
    db = get_db()
    rows = await fjm.list_jobs(
        db, effective["user_id"], limit=min(50, max(1, int(limit))),
        status=status,
    )
    return {"ok": True, "jobs": rows, "count": len(rows)}


# ─── Iter 212m-128 — Restart an orphaned / failed job ─────────────────
@router.post("/restart/{job_id}")
async def restart_job(job_id: str,
                      authorization: Optional[str] = Header(None)) -> dict:
    """Spin a fresh worker on the findings that were NOT yet
    completed by the original job.  Returns a NEW job_id (with a
    clean event stream) — the old row stays in Mongo as historical
    audit.

    Rules:
      • Job must be `orphaned`, `failed`, or `running` (the latter
        covers the case where a different pod's worker is *thought*
        to still be running but the user has lost faith).
      • The caller must own the job.
      • If every finding was already completed, returns 200 with
        `{nothing_to_do: true}` instead of starting an empty worker.
    """
    user, effective = await _gate(authorization, "write")
    user_id = effective["user_id"]
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    persisted = await fjm.get_persisted(db, job_id, user_id)
    if not persisted:
        raise HTTPException(404, "Job not found")
    if persisted.get("status") not in ("orphaned", "failed", "running"):
        raise HTTPException(409, {
            "error":   "job_not_restartable",
            "status":  persisted.get("status"),
            "message": "Only orphaned, failed, or running jobs can be restarted.",
        })

    all_findings = persisted.get("all_findings") or []
    completed    = set(persisted.get("completed_ids") or [])
    fail_terminal = set(persisted.get("failed_terminal_ids") or [])
    # Remaining = original list minus anything already finished
    # successfully OR terminally failed (where retry can't help).
    remaining = [
        f for f in all_findings
        if (f.get("id") or f.get("rule_id")) not in completed
        and (f.get("id") or f.get("rule_id")) not in fail_terminal
    ]
    if not remaining:
        # Flip the old row to "done" so the UI list stops nagging.
        try:
            await db.fix_jobs.update_one(
                {"job_id": job_id, "user_id": user_id},
                {"$set": {"status": "done",
                          "message": "All findings already completed."}},
            )
        except Exception:
            pass
        return {"ok": True, "nothing_to_do": True,
                "message": "All findings were already completed."}

    project_id = persisted.get("project_id") or ""
    quota = await get_fix_quota(effective)
    is_unlim   = quota["is_unlimited"]
    new_job_id = await fjm.create_job(
        db=db, user_id=user_id, kind="bulk", total=len(remaining),
        project_id=project_id, findings=remaining,
    )
    # Mark the original row as superseded so /list doesn't show it
    # alongside the new one.
    try:
        await db.fix_jobs.update_one(
            {"job_id": job_id, "user_id": user_id},
            {"$set": {"superseded_by": new_job_id,
                      "status":        "restarted",
                      "closed_at":     time.time()}},
        )
    except Exception:
        pass
    asyncio.create_task(_run_bulk_job(
        job_id=new_job_id, db=db, user=effective, project_id=project_id,
        findings=remaining, is_unlim=is_unlim,
    ))
    return {
        "ok":             True,
        "original":       job_id,
        "job_id":         new_job_id,
        "remaining":      len(remaining),
        "skipped_done":   len(completed),
        "skipped_terminal": len(fail_terminal),
        "stream":         f"/api/aurem-dev/fix-pipeline/stream/{new_job_id}",
    }


# ─── Part 2b R6 — revert endpoints ───────────────────────────────────
_REVERT_RATE_PER_MIN = 20


@router.post("/fixes/{fix_id}/revert")
async def revert_one_fix(
    fix_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    """Undo ONE fix — restores that file to its pre-fix content via a
    new commit on the shared batch branch; never touches the PR or
    any other fix in the batch (H3). Idempotent — reverting an
    already-reverted fix is a no-op success, not an error."""
    _, effective = await _gate(authorization, "write")
    user_id = effective["user_id"]
    if not await check_rate_limit_async(f"fix-revert:{user_id}", _REVERT_RATE_PER_MIN):
        raise HTTPException(429, "Too many revert requests — slow down.")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    # Resolve the fix's own project so we can fetch a real GitHub
    # token for it — NEVER from the request (R6's explicit ask).
    fix = await db.finding_fixes.find_one(
        {"fix_id": fix_id, "user_id": user_id}, {"_id": 0, "project_id": 1},
    )
    if not fix:
        raise HTTPException(404, "fix_not_found")
    proj = await db.cto_projects.find_one(
        {"project_id": fix["project_id"], "user_id": user_id},
    )
    if not proj:
        raise HTTPException(404, "fix_not_found")
    from services.pat_vault import get_repo_token_or_error
    token, auth_err, auth_detail = await get_repo_token_or_error(proj)
    if not token:
        raise HTTPException(400, {"error": auth_err or "github_credentials_missing",
                                   "detail": auth_detail or ""})

    result = await revert_finding_fix(
        db, fix_id=fix_id, token=token,
        user_id=user_id, project_id=fix["project_id"],
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "fix_not_found" else 400
        raise HTTPException(status, result.get("error") or "revert_failed")
    return result


@router.post("/batches/{batch_id}/revert")
async def revert_one_batch(
    batch_id: str, body: Optional[dict] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Undo an ENTIRE batch — closes the shared PR and reopens every
    finding it resolved. Explicit, separate from a single-fix revert;
    requires `{"confirm": true}` in the body (destructive — closes a
    PR that may carry other, unrelated-looking fixes)."""
    body = body or {}
    if body.get("confirm") is not True:
        raise HTTPException(400, {"error": "confirm_required",
                                   "message": "Pass {\"confirm\": true} to revert the whole batch."})
    _, effective = await _gate(authorization, "write")
    user_id = effective["user_id"]
    if not await check_rate_limit_async(f"batch-revert:{user_id}", _REVERT_RATE_PER_MIN):
        raise HTTPException(429, "Too many revert requests — slow down.")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    from services.fix_batches import get_batch_by_id
    project_id = (body or {}).get("project_id") or ""
    if not project_id:
        # The batch row itself carries project_id — look it up
        # without trusting a client-supplied one for the FIRST read,
        # but get_batch_by_id below still re-validates ownership.
        any_batch = await db.fix_batches.find_one(
            {"batch_id": batch_id, "user_id": user_id}, {"_id": 0, "project_id": 1},
        )
        project_id = (any_batch or {}).get("project_id") or ""
    batch = await get_batch_by_id(db, batch_id=batch_id, user_id=user_id, project_id=project_id)
    if not batch:
        raise HTTPException(404, "batch_not_found")
    proj = await db.cto_projects.find_one({"project_id": project_id, "user_id": user_id})
    if not proj:
        raise HTTPException(404, "batch_not_found")
    from services.pat_vault import get_repo_token_or_error
    token, auth_err, auth_detail = await get_repo_token_or_error(proj)
    if not token:
        raise HTTPException(400, {"error": auth_err or "github_credentials_missing",
                                   "detail": auth_detail or ""})

    result = await revert_batch(
        db, batch_id=batch_id, user_id=user_id, project_id=project_id, token=token,
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "batch_not_found" else 400
        raise HTTPException(status, result.get("error") or "revert_failed")
    return result
