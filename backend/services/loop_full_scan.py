"""
services/loop_full_scan.py  —  Directive Session 2 · Part B (glue layer)
=========================================================================

Loop-Mode-specific plumbing that wires `full_scan_orchestrator.run_full_scan`
into the existing execute → verify → scan → ship pipeline.

Responsibilities (kept intentionally thin so the orchestrator stays
reusable outside Loop Mode):

  1. Persist critical/high self-generated findings to
     `cto_open_findings` (Session 1 collection) so the notification
     strip's backlog surface has real data to draw from later.

  2. Ship-block + 3× auto-retry contract.
       • If Full Scan finds ANY critical/high finding on a file that
         Loop itself just wrote, block Ship and hand the file back to
         the self-heal loop (Parliament healer, existing path).
       • Retry up to `MAX_SCAN_HEALS = 3` times.
       • After the 3rd failed retry, surface to user for manual review
         (paused_for_user event with reason).

  3. Provide a single `get_full_scan_health()` inspection function so
     the dashboard "Verify / Full Scan: Active / Degraded" pattern
     can render honestly per Directive Part B ("dashboard status
     honesty — never claim full coverage falsely").

Everything here is pure-async, no FastAPI context, no globals. All
timing / retry limits live as module constants so they're grep-able
and testable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Retry contract ────────────────────────────────────────────────────
# Same 3-attempt cap the directive specifies. Kept as a module constant
# so tests + docs can reference it by name rather than by literal.
MAX_SCAN_HEALS = 3

# ── Latency guard on the Full Scan itself ─────────────────────────────
# The scanners are pure regex + AST work and are fast — but if a
# pathological repo takes > 30 s we abandon the retry loop and surface
# to the user honestly rather than looping forever.
FULL_SCAN_MAX_SECONDS = 30.0


# ══════════════════════════════════════════════════════════════════════
# Persistence — critical/high findings backlog
# ══════════════════════════════════════════════════════════════════════

def finding_id_for(f: dict) -> str:
    """Deterministic, collision-resistant identity for a scan finding.

    2026-02 fix — two incompatible conventions existed across scanner
    modules and this one function used to assume only the first:
      - Callers that tag their own `scanner` name on every finding
        (full_scan_orchestrator.py's Vanguard/Bug-Hunt/HTTP/Docker
        normalizer, local_tools.py's `ora_chat_audit` save_finding
        tool) use a SHORT rule id/name — the real uniqueness comes
        from wrapping it as `<scanner>::<file>:<line>:<rule>`.
      - codebase_health_core.py / codebase_health_extra.py (the
        customer-facing 16-category scan behind "Run all scanners",
        the /codebase-health page, the in-chat Fix List panel and the
        push-notification strip) never set `scanner` at all — they
        already emit a COMPLETE, globally-unique `id` themselves
        (e.g. "sec::auth.py:42:hardcoded_secret"). The old code always
        wrapped it again as f"{f.get('scanner','unknown')}::...", which
        for these findings produced a double-encoded id like
        "unknown::auth.py:42:sec::auth.py:42:hardcoded_secret" — a
        string that never matched the clean `id` the frontend/fix
        pipeline round-trip back on resolve. Result: every fix applied
        from those 16 categories silently failed to close its
        `cto_open_findings` row, so the SAME already-fixed issue kept
        resurfacing in the Fix List panel and the weekly push-
        notification strip forever (the reported "shows the same
        issue again and again, never removes it after I fix it" bug).
    """
    file_ = f.get("file") or ""
    line_ = int(f.get("line") or 0)
    raw_id = f.get("id")
    scanner = f.get("scanner")
    if scanner:
        rule = f.get("rule_id") or raw_id or "unknown"
        return f"{scanner}::{file_}:{line_}:{rule}"
    if raw_id:
        return str(raw_id)
    rule = f.get("rule_id") or "unknown"
    return f"unknown::{file_}:{line_}:{rule}"


async def persist_findings_to_backlog(
    db,
    *,
    user_id: str,
    project_id: str,
    findings: list[dict],
    scan_source: str = "loop_full_scan",
) -> int:
    """Write critical/high findings into `cto_open_findings` so the
    notification-strip backlog surface (Session 3) has real data.

    Uses upsert keyed on `(user_id, project_id, finding_id)` so the
    same rule triggered on the same line across multiple scans
    increments `last_seen_at` + `exposure_count` bounded at 4
    (per Directive Part D auto-archive rule) instead of duplicating.

    Medium/low findings are intentionally excluded from the backlog
    per the directive — they exist in scan reports but never surface
    on the strip.

    Returns the number of docs written / updated.
    """
    if not findings:
        return 0
    now = datetime.now(timezone.utc)
    count = 0
    for f in findings:
        sev = (f.get("severity") or "").lower()
        # 2026-10 — widened critical/high -> critical/high/medium so
        # the new in-chat "Fix List" panel (routers/findings.py
        # GET /fix-list, reads this same collection, no severity
        # filter) has real coverage of the 9 new "Launch-ready X/16"
        # categories (services/codebase_health_extra.py), most of
        # which are medium severity by design (accessibility, SEO,
        # git hygiene, docs...). The notification-STRIP read path
        # (GET /backlog above) still hard-filters to critical/high
        # only, unchanged — this widening only feeds the on-demand
        # Fix List, never the push-notification cadence.
        # Review fix #2 (Ticket B) — "unknown" is a live OSV vuln with
        # a confirmed real CVE/GHSA id, just an unresolved severity;
        # it belongs in the backlog alongside critical/high/medium,
        # unlike a true informational note (severity "info"/"low").
        if sev not in ("critical", "high", "medium", "unknown"):
            continue
        # Stable, deterministic finding id — same rule on same file+line
        # collapses to one row even across scanner boundaries.
        file_ = f.get("file") or ""
        line_ = int(f.get("line") or 0)
        rule  = f.get("rule_id") or f.get("id") or "unknown"
        finding_id = finding_id_for(f)

        try:
            # $setOnInsert covers first-seen fields; $set covers the
            # values that legitimately refresh on re-detection.
            # $inc caps at 4 via a $min-style upsert would need a
            # pipeline update; simpler: pre-read exposure_count and
            # bump only when < 4. Do it in two steps for clarity.
            existing = await db.cto_open_findings.find_one(
                {"user_id": user_id, "project_id": project_id,
                 "finding_id": finding_id},
                projection={"exposure_count": 1, "status": 1},
            )
            new_exposure = min(
                4, int((existing or {}).get("exposure_count") or 0) + 1
            )
            # If already aged-out, leave it alone — user has to
            # manually resurrect from Settings.
            if (existing or {}).get("status") == "aged-out":
                continue

            await db.cto_open_findings.update_one(
                {"user_id": user_id, "project_id": project_id,
                 "finding_id": finding_id},
                {
                    "$setOnInsert": {
                        "user_id":       user_id,
                        "project_id":    project_id,
                        "finding_id":    finding_id,
                        "first_seen_at": now,
                        "status":        "open",
                    },
                    "$set": {
                        "scanner":         f.get("scanner"),
                        "rule_id":         rule,
                        "category":        f.get("category") or "",
                        "severity":        sev,
                        "file":            file_,
                        "line":            line_,
                        "title":           f.get("title") or "",
                        "message":         f.get("message") or "",
                        "fix_hint":        f.get("fix_hint") or "",
                        "last_seen_at":    now,
                        "last_exposed_at": now,
                        "exposure_count":  new_exposure,
                        "source":          scan_source,
                    },
                },
                upsert=True,
            )
            count += 1
        except Exception as e:                          # noqa: BLE001
            logger.warning(
                "[full-scan] persist failed for %s: %r", finding_id, e,
            )
    return count


async def auto_resolve_stale_findings(
    db,
    *,
    user_id: str,
    project_id: str,
    categories_scanned: list[str],
    still_open_ids: set[str],
) -> int:
    """Self-healing closure for the `cto_open_findings` backlog.

    2026-02 — companion to the `finding_id_for` fix above. Relying only
    on a fix-click's write-back to mark a finding resolved is fragile
    (wrong id format, a fix applied by a different tool/manually on
    GitHub, a scan re-run that simply stops detecting it) — none of
    those paths ever flip `status` away from "open", so an already-
    fixed issue kept resurfacing in the push-notification strip and the
    in-chat Fix List panel forever even after the real code was clean.

    This runs the OTHER direction: after a scan completes, any
    previously "open" finding whose `category` WAS actually covered by
    this scan, but whose `finding_id` is NOT in what this scan just
    detected, is provably gone — close it. Scoped strictly to
    `categories_scanned` so a narrow scan (e.g. the chat `/bug-hunt`
    slash command, one category only) never closes findings in
    categories it didn't even look at.

    Returns the number of findings auto-closed.
    """
    if not categories_scanned:
        return 0
    cur = db.cto_open_findings.find(
        {"user_id": user_id, "project_id": project_id,
         "status": "open", "category": {"$in": categories_scanned}},
        projection={"finding_id": 1},
    )
    to_close = [row["finding_id"] async for row in cur
                if row.get("finding_id") not in still_open_ids]
    if not to_close:
        return 0
    r = await db.cto_open_findings.update_many(
        {"user_id": user_id, "project_id": project_id,
         "finding_id": {"$in": to_close}},
        {"$set": {"status": "fixed",
                   "resolved_at": datetime.now(timezone.utc),
                   "resolved_reason": "no_longer_detected"}},
    )
    return r.modified_count


# ══════════════════════════════════════════════════════════════════════
# Ship-block decision + retry-attempt formatter
# ══════════════════════════════════════════════════════════════════════

def format_retry_message(attempt: int, offending: dict[str, list[dict]]) -> str:
    """One-line status message emitted before each self-heal retry."""
    total_files = len(offending)
    total_findings = sum(len(v) for v in offending.values())
    return (
        f"Full-scan self-heal {attempt}/{MAX_SCAN_HEALS} — "
        f"{total_findings} critical/high issue(s) across "
        f"{total_files} generated file(s)…"
    )


def format_ship_block_reason(offending: dict[str, list[dict]]) -> str:
    """Human-readable reason returned to the user when the retry
    budget is exhausted and Ship remains blocked.

    2026-08-27 — "Show the Outcome, Never the Engine" P1 (M4). Leads
    with a plain-language summary and shows the PLAIN severity scale
    (e.g. "needs your attention") instead of a bare raw label
    ("CRITICAL") — an internal-only-reachable finding is never shown
    at top severity. File paths/lines stay (these are the user's OWN
    generated files, not AUREM internals — legitimate detail, same as
    a ship/confirm card)."""
    total_findings = sum(len(v) for v in offending.values())
    needs_attention = sum(
        1 for hits in offending.values() for h in hits
        if (h.get("plain_severity") or "").lower() == "needs your attention"
    )
    lines = [
        f"Ship blocked: {MAX_SCAN_HEALS} self-heal attempt(s) exhausted. "
        f"{total_findings} issue(s) remain in code I generated for this task"
        + (f", {needs_attention} of which need your attention" if needs_attention else "")
        + ":",
    ]
    for path, hits in sorted(offending.items()):
        lines.append(f"  {path}:")
        for h in hits[:5]:   # cap at 5 per file to keep the message tidy
            rule = h.get("rule_id") or "unknown"
            line = h.get("line") or 0
            plain_sev = h.get("plain_severity") or (h.get("severity") or "").upper()
            msg  = (h.get("message") or "").strip()
            if len(msg) > 140:
                msg = msg[:137] + "…"
            lines.append(f"    L{line} [{plain_sev}] {rule}: {msg}")
        if len(hits) > 5:
            lines.append(f"    …and {len(hits) - 5} more.")
    lines.append(
        "Please review manually — do not force-Ship. The Full Scan is a "
        "safety net; if these are truly false positives, mark them "
        "explicitly in code with `# vanguard: ignore` and re-run Loop."
    )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Health / degraded surface
# ══════════════════════════════════════════════════════════════════════

# Module-scoped health cache — updated on every Full Scan run so a
# dashboard poll can read the last-known status without re-triggering
# a scan. Not thread-safe by design (asyncio single-loop) — every
# writer is the running Loop coroutine.
_LAST_HEALTH: dict = {
    "status":           "unknown",           # "ok" | "degraded" | "error"
    "scanner_status":   {},
    "last_run_at":      None,
    "last_elapsed_s":   0.0,
    "last_finding_count": 0,
}


def record_scan_health(result: dict) -> None:
    """Update the cached health record after a Full Scan run."""
    status = "degraded" if result.get("degraded") else "ok"
    _LAST_HEALTH.update({
        "status":             status,
        "scanner_status":     dict(result.get("scanner_status") or {}),
        "last_run_at":        datetime.now(timezone.utc).isoformat(),
        "last_elapsed_s":     float(result.get("elapsed_seconds") or 0.0),
        "last_finding_count": int(result.get("summary", {}).get("total") or 0),
    })


def get_full_scan_health() -> dict:
    """Read-only view of the current Full-Scan health. Consumed by the
    dashboard "Verify: Active/Degraded" strip so it can honestly
    reflect scanner availability instead of assuming all-green."""
    return dict(_LAST_HEALTH)


def reset_health_for_tests() -> None:
    """Test-only reset. Never called by production code paths."""
    _LAST_HEALTH.update({
        "status": "unknown", "scanner_status": {},
        "last_run_at": None, "last_elapsed_s": 0.0,
        "last_finding_count": 0,
    })
