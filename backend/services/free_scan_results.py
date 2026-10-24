"""
services/free_scan_results.py — Master Prompt v2 PART 4.1.

Wraps the EXISTING auto-triggered scan pipeline
(services/project_onboarding_scan.py::run_onboarding_scan — unchanged
trigger, still fires once on repo connect) with:
  - a cost-capped category runner (run_categories_with_cost_cap):
    stops starting NEW categories once the cumulative estimated cost
    would exceed FREE_SCAN_COST_CAP_USD, marking the rest "not run" —
    never raises, never fails the whole scan, always returns whatever
    findings the categories that DID run produced.
  - a rolling-average cost tracker + >5x alarm (compute_cost_alarm),
    read from this module's own persisted results, not a separate
    collection.
  - full-severity persistence (`free_scan_results`, one doc per
    project — NOT the same as `cto_open_findings`, which only keeps
    critical/high/medium/unknown per services/loop_full_scan.py's own
    documented rule) so the results PAGE can show every issue.
  - the one-free-scan-per-project-per-account guard: start_free_scan
    is a single atomic upsert keyed on project_id alone, so a repeat
    call (retry, race, or an attacker trying to re-trigger) is a
    no-op — only the FIRST caller for a given project ever gets
    started=True.
"""
from __future__ import annotations

import logging
import time

from config.free_scan import COST_ALARM_MULTIPLE

logger = logging.getLogger(__name__)


def run_categories_with_cost_cap(
    categories: dict, text_cache: dict, *, cap_usd: float, cost_per_category_usd: float,
) -> tuple[list[dict], list[str], list[str], float]:
    """Pure, synchronous. Returns (findings, categories_run,
    categories_not_run, cumulative_cost_usd). A category that raises
    is counted as "run" (its cost was still incurred) but contributes
    no findings — same error-swallowing contract as the pre-existing
    per-category try/except in run_onboarding_scan."""
    findings: list[dict] = []
    categories_run: list[str] = []
    categories_not_run: list[str] = []
    cumulative = 0.0
    for name, scan_fn in categories.items():
        if cumulative + cost_per_category_usd > cap_usd:
            categories_not_run.append(name)
            continue
        try:
            findings.extend(scan_fn(text_cache))
        except Exception as e:
            logger.debug("[free-scan] category %s failed: %r", name, e)
        cumulative += cost_per_category_usd
        categories_run.append(name)
    return findings, categories_run, categories_not_run, cumulative


def compute_cost_alarm(cost_usd: float, rolling_avg_usd: float | None) -> bool:
    if not rolling_avg_usd:
        return False
    return cost_usd > COST_ALARM_MULTIPLE * rolling_avg_usd


async def _rolling_avg_cost_usd(db, *, limit: int = 20) -> float | None:
    rows = await db.free_scan_results.find(
        {"status": "ready", "cost_usd": {"$ne": None}}, {"_id": 0, "cost_usd": 1},
    ).sort("completed_at", -1).limit(limit).to_list(limit)
    costs = [r["cost_usd"] for r in rows]
    if not costs:
        return None
    return sum(costs) / len(costs)


async def start_free_scan(db, *, user_id: str, project_id: str) -> bool:
    """Atomic one-per-project guard. Returns True only for the call
    that actually created the doc."""
    res = await db.free_scan_results.update_one(
        {"project_id": project_id},
        {"$setOnInsert": {
            "project_id": project_id, "user_id": user_id,
            "status": "scanning", "started_at": time.time(),
        }},
        upsert=True,
    )
    return res.upserted_id is not None


def _severity_counts(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        sev = (f.get("severity") or "unknown").lower()
        counts[sev] = counts.get(sev, 0) + 1
    return counts


async def finish_free_scan(
    db, *, user_id: str, project_id: str, score, findings: list[dict],
    categories_run: list[str], categories_not_run: list[str], cost_usd: float,
) -> None:
    rolling_avg = await _rolling_avg_cost_usd(db)
    alarm = compute_cost_alarm(cost_usd, rolling_avg)
    issues = [
        {
            "name": f.get("rule_id") or f.get("id") or "issue",
            "severity": (f.get("severity") or "unknown").lower(),
            "file": f.get("file"),
            "line": f.get("line"),
        }
        for f in findings
    ]
    await db.free_scan_results.update_one(
        {"project_id": project_id},
        {"$set": {
            "project_id": project_id, "user_id": user_id,
            "status": "ready", "score": score,
            "issues": issues, "severity_counts": _severity_counts(findings),
            "categories_run": categories_run, "categories_not_run": categories_not_run,
            "cost_usd": round(cost_usd, 6), "rolling_avg_cost_usd": rolling_avg,
            "cost_alarm": alarm, "completed_at": time.time(),
            # The share card deliberately carries ONLY the score — no
            # issue names/files/repo strings ever end up in a URL or
            # OG-tag-rendered surface.
            "share_card": {"score": score},
        }},
        upsert=True,
    )


async def mark_free_scan_error(db, *, project_id: str) -> None:
    """Best-effort — if the repo fetch itself fails (e.g. GitHub
    unreachable) after start_free_scan already claimed the one-per-
    project slot, the results page must show "error", not stay stuck
    on "scanning" forever."""
    try:
        await db.free_scan_results.update_one(
            {"project_id": project_id},
            {"$set": {"status": "error", "completed_at": time.time()}},
        )
    except Exception as e:
        logger.warning("[free-scan] failed to mark project=%s as error: %r", project_id, e)


async def get_free_scan_results(db, *, user_id: str, project_id: str) -> dict | None:
    """IDOR-safe by construction: the project must belong to user_id,
    same ownership check every other project-scoped endpoint in
    routers/cto_projects/management.py already uses."""
    proj = await db.cto_projects.find_one({"project_id": project_id, "user_id": user_id})
    if not proj:
        return None
    return await db.free_scan_results.find_one({"project_id": project_id}, {"_id": 0})
