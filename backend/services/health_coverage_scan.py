"""
services/health_coverage_scan.py — Health Score: Test Coverage
instrumentation (2026-08-23).

Runs a bounded, keyword-filtered slice of the backend pytest suite
with --cov, persists real coverage % (over that slice) + critical-
module integration-test evidence to `health_test_coverage_runs`.

Scoped deliberately — the full ~5,300-test suite with --cov
instrumentation was tried first and did not complete within 600s in
this environment (confirmed by direct measurement, 2026-08-23); a
10+ minute on-demand admin action is impractical, and re-attempting
the full suite is out of scope for this instrumentation pass. The
`scope` field in the persisted doc always discloses this is NOT
whole-repo coverage. On-demand only (admin "Run now" button) — no
default recurring cron, to avoid unplanned resource use on the pod.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

logger = logging.getLogger("health_coverage_scan")

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COVERAGE_JSON_PATH = os.path.join(BACKEND_ROOT, "coverage.json")
# 2026-09-07 — measured wall time of the scoped run is ~430 s on the
# preview pod (855 tests + coverage instrumentation); the old 240 s cap
# meant every widget-triggered run timed out and nothing was persisted.
SUBPROCESS_TIMEOUT_S = int(os.environ.get("HEALTH_COVERAGE_TIMEOUT_S", "900"))

# Curated, real critical modules (auth, chat, findings, fix pipeline,
# payments) — same modules the founder named when scoping this category.
CRITICAL_MODULES = [
    "cto_services/auth.py",
    "routers/chat/stream.py",   # routers/chat.py was split into a package (2026-08)
    "routers/findings.py",
    "routers/fix_pipeline.py",
    "routers/admin_payments.py",
]

# Keyword filter bounding the run to tests touching the critical-path
# areas above (auth/chat/findings/fix-pipeline/payments) — real tests,
# real assertions, just not the entire repo's test suite in one shot.
_SCOPE_KEYWORD_EXPR = "auth or chat or findings or fix_pipeline or payment"


# 2026-09-20 — concurrency guard (real root cause of a live "timeout
# of 40000ms exceeded" report on the Admin Cockpit health-score
# widget). `POST /admin/health-score/test-coverage/run` had NO
# server-side check at all — every call unconditionally launched a
# brand-new pytest+coverage subprocess via `asyncio.create_task`,
# with only a client-side `disabled={running}` button guard that
# resets on page reload / a second tab. Confirmed live: two overlapping
# pytest+coverage subprocesses (each real fork/exec CPU work, not
# just an asyncio await) ended up running simultaneously on this pod,
# and GET /admin/health-score latency degraded from a normal
# sub-second response to 4-6.5s per poll under that load — with the
# widget's own 15s-interval poller (`timeout: 40000` in
# HealthScoreWidget.jsx) eventually tripping the 40s ceiling. A Mongo-
# backed lock (atomic via a duplicate-key upsert, with a stale-lock
# self-heal window) makes a second concurrent run a fast no-op
# instead of a second heavy subprocess.
_LOCK_COLLECTION = "health_coverage_scan_lock"
_LOCK_ID = "singleton"


async def is_scan_in_progress(db) -> bool:
    """Cheap, non-atomic read for UX purposes (e.g. the trigger
    endpoint returning an immediate 409). NOT the actual concurrency
    guard — see `_try_acquire_lock` for the atomic one."""
    if db is None:
        return False
    doc = await db[_LOCK_COLLECTION].find_one({"_id": _LOCK_ID})
    if not doc:
        return False
    return (time.time() - doc.get("locked_at_ts", 0)) < SUBPROCESS_TIMEOUT_S + 60


async def _try_acquire_lock(db) -> bool:
    """Atomic acquire: succeeds if no lock doc exists, or the existing
    one is stale (older than the subprocess timeout + a safety
    margin — self-heals if a prior run died without releasing it).
    Relies on `_id` uniqueness: the upsert's implicit insert attempt
    raises DuplicateKeyError when a fresh lock already holds `_id`,
    which is exactly the "someone else is already running" signal."""
    if db is None:
        return True  # no db to coordinate through — don't block the run
    from pymongo.errors import DuplicateKeyError
    now_ts = time.time()
    stale_before = now_ts - (SUBPROCESS_TIMEOUT_S + 60)
    try:
        await db[_LOCK_COLLECTION].find_one_and_update(
            {"_id": _LOCK_ID, "locked_at_ts": {"$lt": stale_before}},
            {"$set": {"locked_at_ts": now_ts,
                      "locked_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:
        return False


async def _release_lock(db) -> None:
    if db is None:
        return
    try:
        await db[_LOCK_COLLECTION].delete_one({"_id": _LOCK_ID})
    except Exception:
        pass


def _parse_pytest_summary(text: str) -> dict:
    counts = {"passed": 0, "failed": 0, "errors": 0}
    # Only read pytest's FINAL summary line ("=== 855 passed, 32 failed ...
    # in 428s ==="). Scanning the whole log used to match log noise such
    # as "...:6379 ... error" (Redis port in a rate-limiter warning) and
    # persisted a phantom errors=6379 into the evidence (2026-09-07).
    summary_lines = re.findall(r"^(?:=+ )?\d+ (?:passed|failed).*? in [\d.]+s.*$", text, re.M)
    if summary_lines:
        text = summary_lines[-1]
    for key, pattern in (
        ("passed", r"(\d+) passed"),
        ("failed", r"(\d+) failed"),
        ("errors", r"(\d+) error"),
    ):
        m = re.search(pattern, text)
        if m:
            counts[key] = int(m.group(1))
    return counts


def classify_pytest_summary(summary: dict) -> str:
    """2026-09-21 — decision-point audit (Part 2, repo-identity/
    coverage-scan lock-in). Extracted from the inline check that used
    to live directly in `_do_run_coverage_scan` — same rule, now a
    named, independently-testable function: locked by
    tests/unit/coverage_scan_test.py.

    "0 tests observed" (0 passed, 0 failed, 0 errors) means the
    subprocess output couldn't be trusted — treat as `"fail"` rather
    than silently trusting a possibly-stale coverage.json on disk. Any
    real test count, however small, is a trustable `"pass"` (the
    caller still separately checks coverage_pct before persisting a
    score — this function only classifies the pytest-summary parse
    itself)."""
    total = summary.get("passed", 0) + summary.get("failed", 0) + summary.get("errors", 0)
    return "fail" if total == 0 else "pass"


async def run_coverage_scan(db) -> dict:
    """Public entrypoint — wraps `_do_run_coverage_scan` with the
    concurrency guard so a second overlapping trigger is a fast
    no-op instead of a second real pytest+coverage subprocess."""
    if not await _try_acquire_lock(db):
        logger.info("health_coverage_scan: skipped — a run is already in progress")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "a coverage scan is already in progress — poll GET "
                     "/health-score for the run already under way instead "
                     "of retriggering",
        }
    try:
        return await _do_run_coverage_scan(db)
    finally:
        await _release_lock(db)


async def _do_run_coverage_scan(db) -> dict:
    started = time.time()
    cmd = [
        sys.executable, "-m", "pytest", "-q", "--timeout=30",
        "-k", _SCOPE_KEYWORD_EXPR,
        "--cov=.", "--cov-report=json",
        "--continue-on-collection-errors",
        "--ignore=tests/test_iter138_acceptance_seven.py",
        "--ignore=tests/test_iter212m163_aggression_chat.py",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=BACKEND_ROOT,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SUBPROCESS_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("health_coverage_scan timed out — not persisting a score")
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "error": f"timed out after {SUBPROCESS_TIMEOUT_S}s — run incomplete",
                "duration_s": round(time.time() - started, 1),
            }
        out_text = (stdout or b"").decode(errors="replace")
    except Exception as e:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False, "error": f"subprocess failed: {e!r}",
        }

    summary = _parse_pytest_summary(out_text)
    total_tests_seen = summary["passed"] + summary["failed"] + summary["errors"]
    if classify_pytest_summary(summary) == "fail":
        logger.warning("health_coverage_scan: 0 tests observed in output — not trusting "
                        "any coverage.json on disk (could be stale). tail=%r", out_text[-800:])
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "pytest run reported 0 passed/failed/errors — treating as a failed "
                     "run rather than trusting a possibly-stale coverage.json",
            "stdout_tail": out_text[-800:],
            "duration_s": round(time.time() - started, 1),
        }

    coverage_pct = None
    per_file: dict[str, float] = {}
    try:
        cov_mtime = os.path.getmtime(COVERAGE_JSON_PATH)
        if cov_mtime < started:
            raise RuntimeError(
                f"coverage.json mtime ({cov_mtime}) predates this run's start "
                f"({started}) — stale file, not overwritten by this run",
            )
        with open(COVERAGE_JSON_PATH) as fh:
            cov = json.load(fh)
        coverage_pct = round(cov.get("totals", {}).get("percent_covered", 0), 1)
        for path, info in (cov.get("files") or {}).items():
            rel = path.replace(BACKEND_ROOT + "/", "")
            per_file[rel] = round(info.get("summary", {}).get("percent_covered", 0), 1)
    except Exception as e:
        logger.warning("coverage.json parse failed: %r", e)

    critical_modules = []
    for mod in CRITICAL_MODULES:
        pct = per_file.get(mod)
        critical_modules.append({
            "module": mod, "covered_pct": pct,
            "has_integration_test": bool(pct and pct > 0),
        })
    critical_hit_frac = (
        sum(1 for m in critical_modules if m["has_integration_test"]) / len(critical_modules)
        if critical_modules else 0.0
    )

    commit_sha = None
    try:
        from services.deploy_logger import get_current_commit
        # 2026-09-20 — deploy-health fix, round 4. `get_current_commit()`
        # runs up to 5 sequential blocking `subprocess.check_output`
        # git calls. This was the one remaining unwrapped call site
        # after `main.py`'s `/api/health` and `deploy_logger.log_
        # deploy_event()` were fixed for the same class of bug
        # (nginx "upstream timed out ... /health" caused by a
        # blocking subprocess call stalling the shared event loop).
        # `run_coverage_scan` is admin-triggered (on-demand, no cron)
        # but still shares the loop with every other concurrent
        # request, including health probes, while it runs.
        commit_sha = (await asyncio.to_thread(get_current_commit)).get("commit_sha")
    except Exception:
        pass

    doc = {
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "ok":                   coverage_pct is not None,
        "scope":                "keyword-filtered critical-path subset "
                                 f"({_SCOPE_KEYWORD_EXPR}) — NOT whole-repo coverage",
        "commit_sha":           commit_sha,
        "overall_coverage_pct": coverage_pct,
        "critical_modules":     critical_modules,
        "critical_hit_frac":    round(critical_hit_frac, 3),
        "test_counts":          summary,
        "duration_s":           round(time.time() - started, 1),
        "source":               "live_run_in_pod",
    }
    if db is not None:
        try:
            await db.health_test_coverage_runs.insert_one(dict(doc))
        except Exception as e:
            logger.warning("persist coverage run failed: %r", e)
    return doc
