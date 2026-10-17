"""
routers/codebase_health.py  —  Iter 212m-72 (Phase 2)
=====================================================
Five-category codebase health scanner.  Founder-facing endpoint
powering `/codebase-health` dashboard.

Endpoints (all founder-gated, project_id required):

  POST /api/aurem-dev/codebase-health/scan
       Body: { project_id, categories: [security, performance,
                                       code_quality, dependencies,
                                       database] }
       Returns: { score: 0-100, summary, breakdown: {<cat>: {...}} }

  POST /api/aurem-dev/codebase-health/fix
       Body: { project_id, finding_id, category }
       Creates a `cto_task` with the fix prompt + auto-runs it
       through the existing Loop pipeline.  Returns task_id.

All five category scanners are PURE deterministic static analysers
that walk the user's connected GitHub repo via the existing
`_list_repo_tree` + `_fetch_file` helpers from `security_scan.py`.
Zero LLM cost on the scan path — only the Fix button pays an LLM call
when the user actually wants ORA to write the patch.

The repo walk + file fetch is the shared bottleneck across all 5
scanners, so `scan()` does it ONCE then dispatches the cached
`{path: text}` dict to each requested category.  This means a
"Full scan" (all 5) costs the same GitHub-API budget as a single
category — just 4× more CPU on the user's static analysis.
"""
# arch: allow-http — GitHub tree walk (calls _gh_get service) (iter 212m-225)
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cto_services.auth import current_dev, require_admin
from cto_services.db import get_db
from services.feature_flags import is_enabled
from services.team_permissions import check_permission

router = APIRouter(prefix="/codebase-health", tags=["Codebase Health"])

# Build Prompt v4 · Phase B — persistent scan-strip rollout flag. Default
# OFF; ON only for the allowlisted test account, same pattern as Phase A's
# workcard_first_scan. Reused on the ALREADY-EXISTING GET /last endpoint
# (no new endpoint) so ScanStatusStrip can rehydrate a durable, DB-backed
# receipt — including a clean scan — instead of the flag-off legacy
# sessionStorage-only path (which explicitly self-deletes on clean).
_WORKCARD_STRIP_FLAG = "workcard_scan_strip"
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Iter arch-2a — the 6 category scanners + SCANNERS + scoring helpers
# now live in services/codebase_health_core.py (pure functions, no
# router deps) so services/project_onboarding_scan.py can import them
# without an inverted service→router dependency. This module still
# imports them back for its own /scan and /fix endpoints below —
# behaviour is unchanged, only the ownership moved.
# ──────────────────────────────────────────────────────────────────────
from services.codebase_health_core import (                # noqa: E402
    SCANNERS, _score_for_findings,
    _category_label, resolve_live_cves,
)


# The 6 category scanners, SCANNERS, _is_dockerfile, and the scoring
# helpers (_norm_sev/_score_for_findings/_category_label) used to be
# defined here. Iter arch-2a relocated them verbatim to
# services/codebase_health_core.py (imported above) to fix a
# service→router boundary violation. See that module for the actual
# implementations — nothing below this point was changed.


# Text-cache builder lives in services/repo_text_cache.py (2026-09-07
# extraction); kept importable here under its historical name.
from services.repo_text_cache import build_text_cache as _build_text_cache  # noqa: E402

@router.get("/cache-stats")
async def cache_stats(authorization: Optional[str] = Header(None)) -> dict:
    """Iter 212m-79 — surface Redis scan-cache hit-rate to founders.
    Iter 212m-158 — was a custom is_admin check; now routed through
    the shared `require_admin` helper for consistency."""
    await require_admin(authorization)
    from services.scan_cache import get_scan_cache_stats
    return get_scan_cache_stats()


@router.post("/scan")
async def scan(
    body: dict, authorization: Optional[str] = Header(None),
) -> dict:
    # 2026-08-22 fix — was require_admin (Iter 212m-158), but the
    # frontend route guard was ALREADY relaxed to <PrivateRoute> (any
    # logged-in user) back in a later iteration, and this handler
    # already fully scopes by (project_id, user_id) + has its own
    # rate limiting for non-founder accounts (line ~672) — the
    # admin-only gate here was leftover from before that frontend
    # change and silently 403'd every real paying customer who
    # clicked "Review findings →" from the chat reminder banner.
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    # 2026-09-26 P0 RBAC fix (Task 2) — was keyed on the caller's own
    # login for the project-ownership check, rate limit, and
    # fixed-findings map, so a team member with real "projects"
    # access got a false 404/empty state instead of the account's
    # real scan data.
    ctx = await check_permission(user, "projects", "write", db=db)
    user_id = ctx["effective_account_id"]
    project_id = (body or {}).get("project_id")
    categories = (body or {}).get("categories") or list(SCANNERS.keys())
    categories = [c for c in categories if c in SCANNERS]
    if not project_id:
        raise HTTPException(400, "project_id required")
    if not categories:
        raise HTTPException(400, "At least one category required")

    # Iter 212m-75 — sliding-window rate limit (10 scans / hour / user / category).
    # Iter 212m-110 — admins, founders and is_unlimited accounts are
    # ALL exempt. Each call writes one log row to `scan_rate_limits`;
    # the prune step deletes rows older than the window so the
    # collection stays small. Returns 429 with `retry_after_seconds`
    # on the first denied category so the client can wait the right
    # amount.
    is_admin = bool(
        user.get("is_admin")
        or user.get("is_unlimited")
        or (user.get("tier") == "founder")
    )
    if not is_admin:
        denied_cat, retry_secs, remaining = await _check_scan_rate_limit(
            db, user_id, categories,
        )
        if denied_cat is not None:
            mins = max(1, int(round(retry_secs / 60.0)))
            raise HTTPException(429, {
                "error":               "scan_rate_limited",
                "category":            denied_cat,
                "message":             (f"You have used 10/10 scans for "
                                        f"'{denied_cat}' this hour. Try again "
                                        f"in {mins} minutes."),
                "retry_after_seconds": int(retry_secs),
            })
    else:
        remaining = {c: 999 for c in categories}

    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_owner": 1, "github_repo": 1, "github_token": 1,
         "github_branch": 1, "auth_method": 1, "installation_id": 1, "user_id": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    branch = proj.get("github_branch") or "main"
    # 2026-02-11 · Phase 3b (Bug 2 fix) — dual-auth token resolver.
    from services.pat_vault import get_repo_token, GithubAppAuthError
    # 2026-09-09 fix — this call had ZERO exception handling: any
    # GithubAppAuthError (missing/revoked install, transient GitHub
    # 401/403 on the App token mint) crashed unhandled all the way
    # through FastAPI's middleware stack, surfacing to the client as
    # a raw "client disconnected or upstream error" instead of a
    # clean, actionable message — and the scan never got far enough
    # to compute/persist a score, so the Codebase Health page stayed
    # stuck on "unscanned" forever. Mirrors the try/except pattern
    # already used for `_build_text_cache` right below.
    try:
        pat = await get_repo_token(proj)
    except GithubAppAuthError as e:
        status = 400 if e.code in ("app_installation_missing", "app_installation_revoked") else 502
        raise HTTPException(status, e.detail)
    if not (owner and repo and pat):
        raise HTTPException(400, "Project missing GitHub linkage / PAT")

    try:
        text_cache = await _build_text_cache(owner, repo, pat)
    except HTTPException:
        # Iter 212m-216 — meaningful GH errors already carry the
        # right status + detail from `_gh_get`.  Do NOT re-wrap them
        # as 502 (that's what caused Cloudflare to intercept and
        # replace the body with its own "Bad gateway" HTML on prod
        # for months).  Just log + propagate.
        raise
    except Exception as e:
        # Genuine unexpected crash — log full context for founder
        # monitoring, but return a caller-actionable 502 with the
        # actual exception class in the detail so a screenshot alone
        # is enough to root-cause.
        logger.exception(
            "codebase_health.scan crashed inside _build_text_cache "
            "(user=%s, project=%s, owner=%s, repo=%s)",
            user_id, project_id, owner, repo,
        )
        raise HTTPException(
            502,
            f"github_fetch_crashed: {type(e).__name__}: {str(e)[:200]}",
        )

    # 2026-09-27 P0-2 — VERIFIED root cause of "banner shows 'Scan
    # running', then no new result card appears": `_build_text_cache`
    # fetches every candidate file in parallel (asyncio.gather) and
    # silently drops any that fail — a GitHub secondary rate-limit or
    # transient network blip hitting most/all of those parallel
    # fetches at once returns `{}` with NO exception raised anywhere
    # in this call chain. Before this fix, `{}` flowed straight into
    # the scoring logic below, which scores an empty finding set as a
    # perfect "0 issues found — clean" success — a fabricated result
    # that gets a green toast and a chat message, while the line
    # further down that refuses to persist a 0-file scan (Iter
    # 212m-177) correctly skips writing a DB record. Net effect: a
    # fake "clean" reply with nothing behind it — exactly the reported
    # "banner ran, no card ever showed up" symptom, and it also means
    # any PAST scan that silently hit this path told the founder their
    # repo was clean when nothing was actually scanned. Never claim a
    # scan succeeded when zero files were read.
    if not text_cache:
        logger.warning(
            "codebase_health.scan read ZERO files — refusing to report a "
            "fake 'clean' result (user=%s project=%s owner=%s repo=%s branch=%s)",
            user_id, project_id, owner, repo, branch,
        )
        raise HTTPException(502, {
            "error": "scan_no_files_read",
            "message": "The scan couldn't read any files from this repository — "
                        "this is almost always a transient GitHub fetch issue "
                        "(rate limit or network blip), not an actually-empty repo. "
                        "Please try again in a moment.",
        })

    breakdown: dict[str, dict] = {}
    all_findings: list[dict] = []
    # Iter 212m-193 — findings already fixed (commits on draft-PR
    # branches) must not resurrect on rescan: split them out, score
    # and count ACTIVE findings only.
    from services.fixed_findings import get_fixed_map, split_findings
    fixed_map = await get_fixed_map(db, user_id=user_id, project_id=project_id)
    total_fixed = 0
    if "dependencies" in categories:
        # Ticket B (2026-02) — pre-warm the live OSV.dev CVE cache
        # before the per-category loop below. Scoped to a try/except
        # here too (belt-and-suspenders on top of resolve_live_cves's
        # own internal catch) so a totally unexpected failure in this
        # pre-warm step can never take down the whole scan endpoint —
        # `_scan_dependencies` falls back to the static `_CVE_DB`
        # either way.
        try:
            await resolve_live_cves(text_cache)
        except Exception:
            logger.warning("[osv] resolve_live_cves pre-warm raised unexpectedly", exc_info=True)
    for cat in categories:
        # 2026-10-10 fix — root cause of the reported Cloudflare 520
        # ("invalid or incomplete response") on "Run all scanners":
        # every SCANNERS[cat] (Vanguard's 25-pattern regex catalog,
        # Bug Hunt's 50+ rules, the Docker CIS checks, etc.) is a
        # plain synchronous function doing pure CPU-bound regex work
        # over the WHOLE repo's text_cache — calling it directly here
        # blocks this process's single event loop for its entire
        # runtime. Running all 7 categories back-to-back on a
        # large repo can block long enough that `/health` itself goes
        # unresponsive, which is exactly the failure mode that gets a
        # pod killed mid-request by the platform's own liveness
        # check — an ABRUPT connection death, not a clean HTTP error,
        # which is what Cloudflare's 520 (vs. a normal 502/504)
        # actually means. `asyncio.to_thread` moves each scanner's
        # CPU work off the event loop so /health and every other
        # concurrent request stay responsive throughout a scan.
        raw_findings = await asyncio.to_thread(SCANNERS[cat], text_cache)
        findings, fixed_findings = split_findings(raw_findings, fixed_map)
        total_fixed += len(fixed_findings)
        # Cap to top 100 per category to keep the response tight.
        sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        findings.sort(key=lambda f: (sev_rank.get(f["severity"], 9),
                                      f.get("file", ""), f.get("line", 0)))
        capped = findings[:100]
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        breakdown[cat] = {
            "score":    _score_for_findings(findings),
            "counts":   counts,
            "total":    len(findings),
            "findings": capped,
            "fixed_count": len(fixed_findings),
            "fixed":       fixed_findings[:100],
        }
        all_findings.extend(findings)

    overall_score = _score_for_findings(all_findings)
    label, tone = _category_label(overall_score)
    total = sum(b["total"] for b in breakdown.values())
    # 2026-09-21 — report-identity fix (see services/health_reporter.py
    # module docstring): the original mis-attribution incident this
    # follows up on was a human reading a pasted report with no repo
    # identity and assuming it was AUREM's own code, when it was really
    # a connected customer repo. Best-effort — a failed SHA lookup must
    # never block the scan response.
    from services.local_tools import _repo_head_sha
    from services.health_reporter import format_identity_header
    try:
        head_sha = await _repo_head_sha(owner, repo, branch, pat)
    except Exception:                                       # noqa: BLE001
        head_sha = None
    identity_header = format_identity_header(owner, repo, branch, head_sha, "customer")
    # 2026-09-21 — Bug D fix (live QA report): this manual scan path
    # (chat slash-commands + the /codebase-health page) never wrote
    # into `cto_open_findings`, so the Advisor sidebar's "N open
    # findings" counter (routers/advisor_context.py, reads ONLY that
    # collection) stayed frozen across every /bug-hunt or /health-scan
    # run — it only ever moved from Loop's full-scan or the one-time
    # onboarding scan. Reuse the SAME writer those two paths already
    # use (upsert keyed on user+project+finding_id, critical/high
    # only, exposure-count capped) so a manual scan's real findings
    # actually reach the real backlog the counter reads from.
    try:
        from services.loop_full_scan import (
            persist_findings_to_backlog, auto_resolve_stale_findings, finding_id_for,
        )
        await persist_findings_to_backlog(
            db, user_id=user_id, project_id=project_id,
            findings=all_findings, scan_source="codebase_health_manual",
        )
        # 2026-02 — self-healing closure. This scan just re-detected
        # every STILL-open issue across `categories`; any finding that
        # was "open" in one of those same categories before this run
        # but isn't among them now is provably fixed (via Aurem's own
        # fix pipeline, a manual commit, or anything else) — close it
        # so it stops resurfacing in the Fix List panel / push strip.
        still_open_ids = {finding_id_for(f) for f in all_findings}
        await auto_resolve_stale_findings(
            db, user_id=user_id, project_id=project_id,
            categories_scanned=list(categories), still_open_ids=still_open_ids,
        )
    except Exception as e:                                 # noqa: BLE001
        logger.debug("open-findings backlog persist failed: %r", e)
    payload = {
        "ok":            True,
        "identity_header": identity_header,
        "score":         overall_score,
        "label":         label,
        "tone":          tone,
        "total":         total,
        "total_fixed":   total_fixed,
        "scanned_files": len(text_cache),
        "summary":       (
            # 2026-09-27 — wording fix: "categories" here counts which
            # of the 4 top-level SCANNERS (vanguard/bug_hunt/http/
            # docker) this particular scan command ran (e.g. /bug-hunt
            # intentionally runs only the bug_hunt scanner) — it has
            # NOTHING to do with Bug Hunt's own 4 internal pattern
            # groups (secrets/vulnerable-code/endpoints/CVE-deps),
            # which all run correctly inside that one scanner
            # regardless of this count. The old wording ("across N
            # categories") was genuinely misread as "only 1 of Bug
            # Hunt's 4 promised categories ran" — it wasn't; this is
            # just clearer.
            f"{total} issues found across {len(categories)} scanner(s) — "
            f"{sum(1 for f in all_findings if f['severity']=='critical')} critical."
            + (f" {total_fixed} already fixed." if total_fixed else "")
        ),
        "breakdown":     breakdown,
        "scan_remaining": remaining,
    }
    # Iter 212m-127 — Persist the scan result so the Dashboard health
    # ring can read the most-recent score via GET /last without paying
    # the full scan cost on every page mount.  Best-effort: a Mongo
    # failure must NEVER block the user-visible scan response.
    scan_record_id: Optional[str] = None
    try:
        # Iter 212m-177 — P1-5: a scan that read ZERO files scores 100
        # trivially and later contradicts real scans (PROD showed
        # 100-HEALTHY vs 0-CRITICAL for the same repo). Never persist it.
        if len(text_cache) > 0:
            _ins = await db.codebase_health_scans.insert_one({
                "user_id":       user_id,
                "project_id":    project_id,
                "score":         overall_score,
                "label":         label,
                "tone":          tone,
                "total":         total,
                "scanned_files": len(text_cache),
                "summary":       payload["summary"],
                "categories":    list(categories),
                "breakdown":     breakdown,
                "created_at":    time.time(),
            })
            # 2026-09-20 — evidence pointer: capture the record's OWN
            # primary key at insert time so the scan-result chat
            # message below can point back at THIS exact document
            # instead of only carrying aggregate prose.
            scan_record_id = str(_ins.inserted_id)
            # Ticket E (2026-02) — per-project health-trend snapshot.
            # Same len(text_cache) > 0 guard as the scan record above:
            # never snapshot a zero-file "scan". Best-effort — see
            # record_health_snapshot's own docstring.
            from services.project_health_trend import record_health_snapshot
            await record_health_snapshot(
                db, project_id=project_id, user_id=user_id,
                score=overall_score, findings=all_findings,
                source="manual_scan",
            )
    except Exception as e:
        logger.debug("codebase_health_scans persist failed: %r", e)
    # Iter 212m-129 — Learning hook: persist a per-rule histogram of
    # this scan run so analytics can later answer "which rules trigger
    # most often for this user / project / across the platform".
    try:
        from services import ora_fix_learning as _ofl
        rule_counts: dict[str, int] = {}
        sev_counts:  dict[str, int] = {
            "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
        }
        for _f in all_findings:
            _rid = (_f.get("rule_id") or _f.get("rule")
                    or _f.get("title") or "unknown")
            rule_counts[_rid] = rule_counts.get(_rid, 0) + 1
            _sv = (_f.get("severity") or "").lower()
            if _sv in sev_counts:
                sev_counts[_sv] += 1
        await _ofl.record_scan_run(
            db, user_id=user_id, project_id=project_id,
            scanner="codebase_health",
            categories=list(categories),
            files_scanned=len(text_cache),
            counts=sev_counts,
            rule_counts=rule_counts,
            duration_ms=None,
            score=overall_score,
        )
    except Exception as _e:
        logger.debug("learning scan-run hook (health) soft-failed: %r", _e)
    # Iter 212m-75 — surface remaining quota per category in a header so
    # callers can render an inline counter without parsing the body.
    # 2026-09-17 — Bug 6 fix (live QA report): slash-command scan
    # results (`/security-scan` etc.) only ever reached the user as a
    # transient toast — nothing landed in the durable chat transcript,
    # unlike every other agent action. That's invisible to anyone who
    # reopens the chat, and to the audit trail — which matters more
    # here than almost anywhere else, since these are security-
    # relevant results. Best-effort, same pattern as the two persist
    # hooks above: a failure here must never break the scan response
    # the user is waiting on.
    session_id = (body or {}).get("session_id")
    if session_id:
        try:
            cats_label = ", ".join(categories)
            headline = f"**Scan complete — {cats_label}**"
            body_lines = [payload["summary"]]
            # 2026-09-21 — Bug E fix (live QA report): list EVERY
            # scanned category, including 0-finding ones. The old
            # code skipped a category here whenever `b["total"]` was
            # falsy, but the header above always counts
            # `len(categories)` (categories RUN, not categories WITH
            # findings) — so a 4-category health-scan where one
            # category came back clean showed "...across 4
            # categories" with only 3 lines underneath, looking like
            # a miscount. Header and body now always agree.
            for cat in categories:
                b = breakdown.get(cat) or {
                    "total": 0, "counts": {"critical": 0, "high": 0},
                }
                body_lines.append(
                    f"- `{cat}`: {b['total']} finding(s) "
                    f"(critical {b['counts']['critical']}, "
                    f"high {b['counts']['high']})"
                )
            evidence_ref = None
            if scan_record_id:
                # 2026-09-20 — pointer + preview (services/evidence_
                # pointer.py). Preview items are REAL findings pulled
                # straight from `all_findings` — never invented —
                # capped to 5 so the message stays a preview, with a
                # stable pointer back to the full scan document for
                # anyone who reopens this chat later.
                from services.evidence_pointer import build_evidence_message
                sev_rank = {"critical": 0, "high": 1, "medium": 2,
                            "low": 3, "info": 4}
                ordered = sorted(
                    all_findings,
                    key=lambda f: (sev_rank.get(f.get("severity"), 9),
                                   f.get("file", ""), f.get("line", 0)),
                )
                preview_items = [
                    f"`{f.get('file', '?')}:{f.get('line', '?')}` — "
                    f"{f.get('title') or f.get('message', '')[:80]}"
                    for f in ordered[:5]
                ]
                msg = build_evidence_message(
                    kind="scan", record_id=scan_record_id,
                    headline=headline, body_lines=body_lines,
                    preview_items=preview_items, preview_total=total,
                )
                content = msg["text"]
                evidence_ref = msg["evidence_ref"]
            else:
                content = "\n".join([headline, ""] + body_lines)
            # 2026-09-21 — Bug A root-cause fix (live QA report, real
            # repro against p_64987820ec/Aurem-1): this used to call
            # services.ora_chat.session.append_message(), which
            # writes into `ora_chat_sessions` — a completely separate
            # collection the REAL product chat never reads. The live
            # UI's GET /chat/history reads db.chat_sessions ONLY
            # (written by services/chat_helpers.py::_persist_turn and
            # routers/chat/misc.py's task-followup writer). Confirmed
            # live: append_message() also has no upsert, so for every
            # real session_id the UI actually uses (which never
            # exists as an ora_chat_sessions doc) the write was a
            # silent no-op — matched_count stayed 0. Net effect: the
            # scan-complete bubble the frontend adds to LOCAL state
            # (ChatPanel.jsx _executeSlashScan) was NEVER durably
            # saved anywhere the real UI could re-fetch it from, so
            # the next GET /chat/history (reload, tab remount, the
            # "Review findings" full-page nav) always came back
            # without it — it visually "vanished" / the chat
            # "reverted". Fix: push directly into db.chat_sessions.
            # turns with the SAME shape + upsert the durable regular-
            # turn writer (_persist_turn) uses, so it is actually
            # there on every future read, same as any other message.
            now = time.time()
            new_turn = {
                "role":     "assistant",
                "content":  content,
                "ts":       now,
                "provider": "scan-result",
                "kind":     "scan_result",
            }
            if evidence_ref:
                new_turn["evidence_ref"] = evidence_ref
            await db.chat_sessions.update_one(
                {"session_id": session_id, "user_id": user_id},
                {
                    "$setOnInsert": {
                        "session_id": session_id, "user_id": user_id,
                        "created_at": now, "project_id": project_id,
                    },
                    "$set": {"updated_at": now, "last_message": content[:120]},
                    "$push": {"turns": {"$each": [new_turn], "$slice": -200}},
                },
                upsert=True,
            )
        except Exception as e:                              # noqa: BLE001
            logger.debug("scan-result chat persist failed: %r", e)
    headers = {
        "X-Scan-Remaining": str(min(remaining.values()) if remaining else 0),
        "X-Scan-Remaining-Per-Category": ",".join(
            f"{c}:{n}" for c, n in remaining.items()
        ),
    }
    return JSONResponse(content=payload, headers=headers)


# ──────────────────────────────────────────────────────────────────────
# Iter 212m-127 — Dashboard health-ring lookup.  Returns the most recent
# persisted scan for the active project so the ring renders instantly
# without re-walking the GitHub tree.  Returns `score: null` (200, not
# 404) when the user hasn't scanned the project yet — the Dashboard
# already treats `null` as "ring hidden".
# ──────────────────────────────────────────────────────────────────────
@router.get("/last")
async def last_scan(
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    if not project_id:
        raise HTTPException(400, "project_id required")
    # 2026-08-22 fix — was require_admin, same regression as /scan
    # above: silently 403'd real paying customers polling their own
    # last scan result. Already scoped to this exact user_id in the
    # query below, so relaxing to current_dev can't leak another
    # user's scan data.
    user = await current_dev(authorization)
    ctx = await check_permission(user, "projects", "read", db=get_db())
    user_id = ctx["effective_account_id"]
    # Build Prompt v4 · Phase B — computed once, included on every branch
    # below so the frontend always gets a consistent boolean regardless
    # of which return path fires.
    workcard_enabled = await is_enabled(_WORKCARD_STRIP_FLAG, user_id=user_id)
    db = get_db()
    if db is None:
        # Don't 503 — frontend silently hides the ring on errors.
        return {"ok": True, "score": None, "workcard_enabled": workcard_enabled}
    try:
        doc = await db.codebase_health_scans.find_one(
            {"user_id": user_id, "project_id": project_id,
             "scanned_files": {"$gt": 0}},   # Iter 212m-177 P1-5
            {"_id": 0},
            sort=[("created_at", -1)],
        )
    except Exception as e:
        logger.debug("codebase_health_scans read failed: %r", e)
        return {"ok": True, "score": None, "workcard_enabled": workcard_enabled}
    if not doc:
        # Empty state — 200 with `score: null` instead of 404 noise.
        return {"ok": True, "score": None, "workcard_enabled": workcard_enabled}
    # Iter 212m-147 — Defensive guard: a persisted (score=0, total=0)
    # row is logically impossible from a real scan (0 findings yields
    # score=100), so it can only come from a legacy bad write or a
    # crashed scan. Treat it as "no scan yet" so the top-bar ring
    # stays hidden instead of misleading the user with a red "0".
    _score = doc.get("score")
    _total = doc.get("total")
    if _score == 0 and (not _total or _total == 0):
        return {"ok": True, "score": None, "workcard_enabled": workcard_enabled}
    return {
        "ok":            True,
        "score":         doc.get("score"),
        "label":         doc.get("label"),
        "tone":          doc.get("tone"),
        "total":         doc.get("total"),
        "scanned_files": doc.get("scanned_files"),
        "summary":       doc.get("summary"),
        "categories":    doc.get("categories") or [],
        # Iter 212m-176 — return the persisted breakdown so the
        # Codebase Health page can restore the last scan on reload
        # instead of showing "unscanned" after the user already paid.
        "breakdown":     doc.get("breakdown") or {},
        "created_at":    doc.get("created_at"),
        "workcard_enabled": workcard_enabled,
    }


@router.get("/trend")
async def health_trend(
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Ticket E (2026-02) — per-project health-score trend, newest
    90 snapshots across every scan source (manual/scheduled/onboarding).
    Same (user_id, project_id) isolation as every other read in this
    router — scoped twice: once via effective_account_id below, and
    again inside get_project_health_trend's own query filter."""
    if not project_id:
        raise HTTPException(400, "project_id required")
    user = await current_dev(authorization)
    ctx = await check_permission(user, "projects", "read", db=get_db())
    user_id = ctx["effective_account_id"]
    db = get_db()
    if db is None:
        return {"ok": True, "snapshots": []}
    from services.project_health_trend import get_project_health_trend
    try:
        snapshots = await get_project_health_trend(
            db, project_id=project_id, user_id=user_id,
        )
    except Exception as e:
        logger.debug("project_health_snapshots read failed: %r", e)
        return {"ok": True, "snapshots": []}
    return {"ok": True, "snapshots": snapshots}


# ──────────────────────────────────────────────────────────────────────
# Iter 212m-75 — Sliding-window scan rate limiter.
#   • Bucket: (user_id, category)
#   • Window: 3600 seconds (1 hour, rolling)
#   • Cap:    10 successful scan starts per bucket
#   • Storage: scan_rate_limits collection (one doc per scan call)
#   • TTL: prune-on-read — every check deletes window-expired rows for
#         the caller so the collection stays bounded.
# ──────────────────────────────────────────────────────────────────────
_SCAN_RATE_WINDOW = 3600
_SCAN_RATE_CAP    = 10


async def _check_scan_rate_limit(
    db, user_id: str, categories: list[str],
) -> tuple[Optional[str], int, dict[str, int]]:
    """Returns (denied_category, retry_after_seconds, remaining_per_cat).

    If any requested category is over cap, returns the *first* one that
    is denied + the seconds until its oldest hit ages out of the window.
    On success, writes one entry per category and returns (None, 0,
    remaining-per-category dict).
    """
    now = time.time()
    cutoff = now - _SCAN_RATE_WINDOW
    coll = db.scan_rate_limits

    # Prune expired entries for this user (cheap — indexed).
    try:
        await coll.delete_many({"user_id": user_id, "ts": {"$lt": cutoff}})
    except Exception as e:
        logger.debug("scan_rate prune failed: %r", e)

    # Count hits per requested category in the current window.
    counts: dict[str, int] = {}
    oldest: dict[str, float] = {}
    for cat in categories:
        cur = coll.find(
            {"user_id": user_id, "category": cat, "ts": {"$gte": cutoff}},
            {"_id": 0, "ts": 1},
        ).sort("ts", 1)
        ts_list = [d["ts"] async for d in cur]
        counts[cat] = len(ts_list)
        if ts_list:
            oldest[cat] = ts_list[0]

    # First over-cap category wins the denial.
    for cat in categories:
        if counts.get(cat, 0) >= _SCAN_RATE_CAP:
            o = oldest.get(cat, now)
            retry = max(1, int((o + _SCAN_RATE_WINDOW) - now))
            remaining = {c: max(0, _SCAN_RATE_CAP - counts.get(c, 0))
                         for c in categories}
            return cat, retry, remaining

    # Allowed — log one entry per category atomically.
    try:
        await coll.insert_many([
            {"user_id": user_id, "category": cat, "ts": now}
            for cat in categories
        ])
    except Exception as e:
        # Storage failure must NEVER block a paying user's scan.
        logger.warning("scan_rate insert failed: %r", e)
    remaining = {
        c: max(0, _SCAN_RATE_CAP - (counts.get(c, 0) + 1)) for c in categories
    }
    return None, 0, remaining


# ──────────────────────────────────────────────────────────────────────
# Fix-button endpoint — creates a cto_task that fixes one finding.
# ──────────────────────────────────────────────────────────────────────
@router.post("/fix")
async def request_fix(
    body: dict, authorization: Optional[str] = Header(None),
) -> dict:
    # Iter 212m-190 — task-quota model: tier gate (health-scan fixes
    # need Pro+), 1 task per successful fix. No token pricing.
    from services.scan_fix_quota import assert_can_fix, record_scan_fixes
    user = await current_dev(authorization)
    project_id  = (body or {}).get("project_id")
    finding_id  = (body or {}).get("finding_id") or ""
    title       = (body or {}).get("title") or "security_issue"
    file_path   = (body or {}).get("file") or ""
    line        = int((body or {}).get("line") or 0)
    message     = (body or {}).get("message") or ""
    fix_hint    = (body or {}).get("fix_hint") or ""
    if not project_id or not finding_id:
        raise HTTPException(400, "project_id and finding_id required")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    # 2026-09-26 P0 RBAC fix (Task 2) — was keyed on the caller's own
    # login for project lookup + shared task quota, so a team member
    # with real "projects" write access either got a false 404 or
    # drew from their own personal quota instead of the account's.
    ctx = await check_permission(user, "projects", "write", db=db)
    user_id = ctx["effective_account_id"]
    effective_user = {**user, "user_id": user_id}
    is_unlimited_user = bool(
        user.get("is_admin")
        or user.get("is_unlimited")
        or (user.get("tier") == "founder")
    )
    # Gate BEFORE any work: tool access + 1 task remaining. Raises
    # 403 fix_not_available_on_tier / 402 insufficient_tasks.
    await assert_can_fix(effective_user, "health-scan", count=1)
    tokens_cost = 0
    me = await db.dev_users.find_one(
        {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1},
    )
    if not me:
        raise HTTPException(404, "User not found")
    new_balance = int(me.get("tokens_remaining") or 0)

    # Iter 212m-114 — REAL fix path. Previously this endpoint just
    # enqueued a cto_tasks record with kind:"health_fix" and returned
    # "Fix queued" (effectively a dummy — no background worker ever
    # consumed it). Now we run the same apply_finding_fix() pipeline
    # the Security Scan /fix uses: fetch file via PAT → LLM patch →
    # re-validate → commit. Tokens are REFUNDED on any failure.
    finding_payload = {
        "rule_id":   finding_id,
        "file":      file_path,
        "line":      line,
        "severity":  "medium",
        "title":     title,
        "message":   message,
        "snippet":   fix_hint,
    }
    from services.finding_fix_applier import apply_finding_fix
    from services import ora_fix_learning as _ofl
    import time as _t
    _t_start = _t.time()
    try:
        res = await apply_finding_fix(
            db=db, user=effective_user, project_id=project_id, finding=finding_payload,
        )
    except Exception as e:
        logger.exception("health apply_finding_fix raised")
        res = {"ok": False, "error": f"unhandled: {e}"}
    _dur_ms = int((_t.time() - _t_start) * 1000)

    # Iter 212m-129 — Learning hook (single-finding codebase-health fix).
    try:
        await _ofl.record_fix_outcome(
            db, user_id=user_id, project_id=project_id,
            finding={**finding_payload, "category": "codebase_health",
                     "scanner": "codebase_health"},
            result=res, attempts=1, duration_ms=_dur_ms,
            tokens_charged=(tokens_cost if res.get("ok") else 0),
            scanner="codebase_health",
        )
    except Exception as _e:
        logger.debug("learning hook (health) soft-failed: %r", _e)

    if not res.get("ok"):
        # Refund tokens if deduction happened (founders deducted=0).
        if not is_unlimited_user and tokens_cost:
            try:
                await db.dev_users.update_one(
                    {"user_id": user_id},
                    {"$inc": {"tokens_remaining": tokens_cost}},
                )
                new_balance += tokens_cost
            except Exception as e:
                logger.warning("health refund failed: %r", e)
        err_code = res.get("error") or "unknown_error"
        if err_code == "patch_did_not_resolve_finding":
            raise HTTPException(422, {
                "error":          err_code,
                "message":        "AI patch did not resolve the finding — no commit pushed, tokens refunded.",
                "tokens_refunded": True,
            })
        if err_code in ("github_credentials_missing", "github_unauthorized"):
            raise HTTPException(401, {
                "error":          err_code,
                "message":        "Connect your GitHub PAT / OAuth before applying fixes.",
                "tokens_refunded": True,
            })
        # Iter 212m-114 (iter_26 follow-up) — match /security-scan/fix:
        # ownership-mismatch and missing-file should be 404, not 500.
        if err_code in ("project_not_found_or_not_yours",
                        "file_not_found", "file_empty_or_missing"):
            raise HTTPException(404, {
                "error":          err_code,
                "tokens_refunded": True,
            })
        raise HTTPException(500, {
            "error":          err_code,
            "tokens_refunded": True,
        })

    # Also persist a row to cto_tasks so the existing audit-log UI
    # surfaces this fix in the activity feed.
    # Iter 212m-190 — deduct exactly 1 task for the successful fix.
    if not is_unlimited_user:
        try:
            await record_scan_fixes(user_id, "health-scan", 1)
        except Exception as _e:
            logger.warning("task record failed (health fix): %r", _e)
    # Iter 212m-193 — persist fixed state so rescans don't resurrect it.
    from services.fixed_findings import record_fixed as _record_fixed
    await _record_fixed(
        db, user_id=user_id, project_id=project_id,
        finding=finding_payload,
        commit_sha=res.get("commit_sha") or "",
        html_url=res.get("html_url") or "",
        tool="health-scan",
    )
    import uuid as _uuid, time as _time
    task_id = f"task_{_uuid.uuid4().hex[:10]}"
    await db.cto_tasks.insert_one({
        "task_id":         task_id,
        "user_id":         user_id,
        "project_id":      project_id,
        "kind":            "health_fix",
        "status":          "completed",
        "finding_id":      finding_id,
        "finding_title":   title,
        "finding_file":    file_path,
        "finding_line":    line,
        "commit_sha":      res["full_sha"],
        "html_url":        res["html_url"],
        "pr_url":          res.get("pr_url"),
        "created_at":      _time.time(),
        "completed_at":    _time.time(),
        "tokens_charged":  tokens_cost,
    })
    return {
        "ok":              True,
        "task_id":         task_id,
        "commit_sha":      res["commit_sha"],
        "full_sha":        res["full_sha"],
        "html_url":        res["html_url"],
        # 2026-09-18 fix — found while live-verifying the Bug 1
        # duplicate-PR fix against a real connected repo
        # (polarisbuiltinc-wq/Aurem-1): `apply_finding_fix()` has
        # always returned `pr_url`, but this endpoint's response
        # silently dropped it — a caller had no way to know a PR was
        # even opened, let alone link to it, unless they went digging
        # in server logs.
        "pr_url":          res.get("pr_url"),
        "tokens_charged":  tokens_cost,
        "new_balance":     new_balance,
        "message":         res["message"],
    }



# ══════════════════════════════════════════════════════════════════════
# Iter 212m-230 — Scanner Feedback Dashboard (Phase 7)
# ══════════════════════════════════════════════════════════════════════
# The fix_triage layer POSTs every false-positive it detects into the
# `scanner_feedback` Mongo collection.  This endpoint aggregates those
# rows into a rule-tuning dashboard: which rules generate the most FPs,
# on which paths, and how the rate trends over time.
#
# Founder story: "your scanners learn from every scan" — the platform's
# self-improving loop is now visible.
# ══════════════════════════════════════════════════════════════════════

@router.get("/scanner-feedback")
async def scanner_feedback(
    days: int = 30,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Aggregate false-positive feedback so a human can identify rules
    that need tuning.  Founder-only.

    Returns:
        {
            "window_days": int,
            "total_fps":   int,
            "by_rule":     [{"rule_id", "count", "example_files"}],
            "by_file":     [{"file", "count", "top_rule"}],
            "trend_daily": [{"date", "count"}],
            "recent":      [{finding + meta}, ...],
        }
    """
    await require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    days = max(1, min(int(days or 30), 180))
    cutoff = time.time() - days * 86400

    # ── Top rules by FP count -------------------------------------
    by_rule_cur = db.scanner_feedback.aggregate([
        {"$match": {"detected_at": {"$gte": cutoff}}},
        {"$group": {
            "_id":            "$finding.rule_id",
            "count":          {"$sum": 1},
            "example_files":  {"$addToSet": "$finding.file"},
        }},
        {"$sort":  {"count": -1}},
        {"$limit": 20},
    ])
    by_rule = []
    async for row in by_rule_cur:
        by_rule.append({
            "rule_id":       row.get("_id") or "unknown",
            "count":         int(row.get("count") or 0),
            "example_files": (row.get("example_files") or [])[:5],
        })

    # ── Top files by FP count -------------------------------------
    by_file_cur = db.scanner_feedback.aggregate([
        {"$match": {"detected_at": {"$gte": cutoff}}},
        {"$group": {
            "_id":       "$finding.file",
            "count":     {"$sum": 1},
            "top_rule":  {"$first": "$finding.rule_id"},
        }},
        {"$sort":  {"count": -1}},
        {"$limit": 20},
    ])
    by_file = []
    async for row in by_file_cur:
        by_file.append({
            "file":     row.get("_id") or "unknown",
            "count":    int(row.get("count") or 0),
            "top_rule": row.get("top_rule") or "",
        })

    # ── Daily trend (14d only) ------------------------------------
    trend_cutoff = time.time() - 14 * 86400
    trend_cur = db.scanner_feedback.aggregate([
        {"$match": {"detected_at": {"$gte": trend_cutoff}}},
        {"$group": {
            "_id":   {"$dateToString": {
                "format": "%Y-%m-%d",
                "date":   {"$toDate": {"$multiply": ["$detected_at", 1000]}},
            }},
            "count": {"$sum": 1},
        }},
        {"$sort":  {"_id": 1}},
    ])
    trend_daily = []
    async for row in trend_cur:
        trend_daily.append({
            "date":  row.get("_id"),
            "count": int(row.get("count") or 0),
        })

    total_fps = await db.scanner_feedback.count_documents({
        "detected_at": {"$gte": cutoff},
    })

    # ── Last 20 recent FPs (for a "sample" panel) ----------------
    recent_cur = db.scanner_feedback.find(
        {"detected_at": {"$gte": cutoff}},
        {"_id": 0, "finding": 1, "detected_at": 1, "source": 1},
    ).sort("detected_at", -1).limit(20)
    recent = [row async for row in recent_cur]

    return {
        "window_days":  days,
        "total_fps":    total_fps,
        "by_rule":      by_rule,
        "by_file":      by_file,
        "trend_daily":  trend_daily,
        "recent":       recent,
        "generated_at": time.time(),
    }


# ── 2026-09-27 P0-2 follow-up — Bug Hunt trigger telemetry ──
# The founder reported "typing/clicking /bug-hunt sometimes fires zero
# network requests" and I could not reproduce it on this pod (both an
# Enter-key attempt and two back-to-back menu clicks fired real scan
# requests every time I tried). Rather than claim it's fixed on a
# symptom I never actually saw, this logs EVERY bug-hunt trigger
# attempt server-side — timestamp, how it was triggered (keydown vs
# click vs the send() fallback guard), and whether the scan POST was
# actually reached — so the NEXT time the founder reproduces it on
# production, there's a real, queryable record instead of relying on
# them having DevTools open at the exact right moment. Deliberately
# NOT marking P0-2's "zero requests" sub-case as fixed — this is
# observability for that specific unreproduced symptom, nothing more.
class BugHuntTriggerEvent(BaseModel):
    trigger:      str            # "keydown_enter" | "menu_click" | "send_exact_match"
    request_sent: bool           # did the code actually reach the scan POST call?
    reason:       Optional[str] = None   # why not, if request_sent is False
    client_ts:    Optional[float] = None  # client Date.now()/1000, for ordering


@router.post("/telemetry/bug-hunt-trigger")
async def log_bug_hunt_trigger(body: BugHuntTriggerEvent,
                                authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": False}
    event = {
        "user_id":      user["user_id"],
        "trigger":      body.trigger,
        "request_sent": body.request_sent,
        "reason":       body.reason,
        "client_ts":    body.client_ts,
        "server_ts":    time.time(),
    }
    await db.bug_hunt_trigger_events.insert_one(event)
    logger.info(
        "bug_hunt_trigger user=%s trigger=%s request_sent=%s reason=%s",
        user["user_id"], body.trigger, body.request_sent, body.reason,
    )
    return {"ok": True}


@router.get("/telemetry/bug-hunt-triggers")
async def list_bug_hunt_triggers(limit: int = 100,
                                  authorization: Optional[str] = Header(None)):
    """Admin-only — inspect recent trigger events to prove/disprove
    the "zero requests fired" symptom once it's actually reproduced."""
    await require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    cur = db.bug_hunt_trigger_events.find(
        {}, {"_id": 0},
    ).sort("server_ts", -1).limit(min(limit, 500))
    return {"events": [row async for row in cur]}
