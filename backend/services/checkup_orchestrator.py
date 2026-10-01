"""
services/checkup_orchestrator.py — runs the checkup and streams stage
progress.  Pure/sync core (`iter_checkup`) so it can be unit-tested and
driven from SSE; `run_checkup_async` moves it off the event loop.

S9: a check that crashes or runs out of time is reported under
`unverified` ("Couldn't verify"), never silently counted as passed.
S11: a wall-clock budget bounds the whole run; remaining checks become
`unverified` instead of blocking.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Iterator, Optional

from services import fix_items as fi
from services.checkup_checks import CHECKS, detect_stack
from services.permission_map import build_permission_map

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_S = 60.0
_SCANNER_SEVERITIES = ("critical", "high")


def _scanner_items(tc: dict[str, str]) -> Iterator[dict]:
    """Existing deterministic scanners, critical/high only (same bar as the
    backlog), re-shaped to fix_items input."""
    from services.codebase_health_core import SCANNERS, _norm_sev
    for name, fn in SCANNERS.items():
        for f in fn(tc) or []:
            sev = _norm_sev(f.get("severity"))
            if sev not in _SCANNER_SEVERITIES:
                continue
            yield {
                "scanner": name, "rule_id": f.get("rule_id") or f.get("id") or "unknown",
                "severity": sev,
                "evidence": {"file": f.get("file") or "", "line": int(f.get("line") or 0),
                             "rule": f.get("rule_id") or f.get("id") or "",
                             "detail": f.get("message") or f.get("title") or ""},
                "fix": {"hint": f["fix_hint"]} if f.get("fix_hint") else None,
                "confidence": "high",
            }


def seo_cards_to_items(cards: list[dict]) -> list[dict]:
    """Fold the SEO first-scan cards into the checkup as category `launch`
    (one item per file card — same granularity the user already saw)."""
    out = []
    for c in cards or []:
        path = c.get("path") or ""
        out.append({
            "scanner": "seo", "rule_id": "seo_page_basics", "severity": "low",
            "stage": "launch", "category": "launch",
            "title_plain": f"{c.get('file_label') or 'A page'} is missing details search engines look for",
            "impact_plain": "Fewer people will find your site on Google.",
            "evidence": {"file": path, "line": 0, "rule": "seo_page_basics",
                         "detail": "; ".join(str(b) for b in (c.get("bullets") or []))[:300]},
            "confidence": "high",
        })
    return out


def iter_checkup(tc: dict[str, str], *, budget_s: float = DEFAULT_BUDGET_S,
                 include_scanners: bool = True,
                 answers: Optional[dict] = None,
                 clock=time.monotonic) -> Iterator[dict]:
    """Yield progress events: stage_started, items, stage_done, then a
    final `done` event with score/stages/unverified."""
    t0 = clock()
    stack = detect_stack(tc)
    yield {"type": "started", "stack": stack["label"]}
    items: list[dict] = []
    unverified: list[dict] = []
    seen: set[str] = set()

    def add(it: dict, stage: str) -> Optional[dict]:
        it = dict(it)
        it.setdefault("stage", stage)
        ev = it.get("evidence") or {}
        key = f"{it.get('scanner')}::{ev.get('file')}:{ev.get('line')}:{it.get('rule_id')}"
        if key in seen:
            return None
        seen.add(key)
        it.setdefault("id", key)
        items.append(it)
        return it

    pmap = build_permission_map(tc, answers)
    plan = list(CHECKS)
    plan.append(("permissions", "permission_map", lambda _tc: pmap["items"]))
    if include_scanners:
        plan.append(("safe", "scanners", lambda tc_: list(_scanner_items(tc_))))
    current = None
    for stage, name, fn in plan:
        if stage != current:
            current = stage
            yield {"type": "stage_started", "stage": stage}
        if clock() - t0 > budget_s:
            unverified.append({"stage": stage, "check": name, "reason": "ran out of time"})
            continue
        try:
            found = fn(tc)
        except Exception as e:                          # noqa: BLE001
            logger.warning("[checkup] check %s crashed: %r", name, e)
            unverified.append({"stage": stage, "check": name, "reason": "check failed to run"})
            continue
        new = [x for x in (add(f, stage) for f in found) if x]
        if new:
            yield {"type": "items", "stage": stage, "items": new}
    yield {
        "type": "done",
        "stack": stack,
        "items": items,
        "score": fi.score_for(items),
        "stages": fi.stage_summary(items),
        "unverified": unverified,
        "permissions": {"supported": pmap["supported"], "reason": pmap.get("reason"),
                        "questions": pmap["questions"], "matrix": pmap["matrix"]},
        "duration_ms": int((clock() - t0) * 1000),
    }


def run_checkup(tc: dict[str, str], **kw) -> dict:
    final = None
    for ev in iter_checkup(tc, **kw):
        if ev["type"] == "done":
            final = ev
    return final or {}


async def run_checkup_async(tc: dict[str, str], **kw) -> dict:
    return await asyncio.to_thread(run_checkup, tc, **kw)
