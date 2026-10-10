"""
services/fix_items_model.py — pure model of the single "things to fix" layer for the
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
from typing import Optional

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


def _first(doc: dict, *keys: str, default=None):
    """First truthy value among `keys` — keeps legacy/new field fallbacks flat."""
    for k in keys:
        if doc.get(k):
            return doc[k]
    return default


def _default_evidence(doc: dict) -> dict:
    return {
        "file": doc.get("file") or "",
        "line": doc.get("line") or 0,
        "rule": doc.get("rule_id") or "",
        "detail": _first(doc, "message", "title", default=""),
    }


def _default_fix(doc: dict) -> Optional[dict]:
    return {"hint": doc["fix_hint"]} if doc.get("fix_hint") else None


def to_fix_item(doc: dict) -> dict:
    """Project a `cto_open_findings` row (old or new shape) into the
    fix_items shape.  Pure; never mutates the input; tolerates rows that
    pre-date the additive fields."""
    scanner = doc.get("scanner")
    rule_id = doc.get("rule_id")
    title_plain, impact_plain = plain_copy(scanner, rule_id)
    stage = stage_for(scanner, rule_id)
    return {
        "id": doc.get("finding_id") or "",
        "project_id": doc.get("project_id"),
        "category": doc.get("category") or stage,
        "stage": doc.get("stage") or stage,
        "severity": normalize_severity(doc.get("severity")),
        "title_plain": doc.get("title_plain") or title_plain,
        "impact_plain": doc.get("impact_plain") or impact_plain,
        "evidence": doc.get("evidence") or _default_evidence(doc),
        "confidence": doc.get("confidence") or "high",
        "fix": doc.get("fix") or _default_fix(doc),
        "status": normalize_status(doc.get("status")),
        "question": doc.get("question"),
        "found_at": _first(doc, "found_at", "first_seen_at"),
        "fixed_at": doc.get("fixed_at"),
        "fixed_by": doc.get("fixed_by"),
        "last_checked_at": _first(doc, "last_checked_at", "last_seen_at"),
        "rule_id": rule_id,
    }


def score_for(items: list[dict]) -> int:
    """Health score of the still-open items — the one score function."""
    from services.codebase_health_core import _score_for_findings
    live = [
        {"severity": normalize_severity(i.get("severity"))}
        for i in items
        if _is_live(i)
    ]
    return _score_for_findings(live)


_LIVE = ("open", "your_answer", "fixing", "regressed")


def _is_live(item: dict) -> bool:
    return normalize_status(item.get("status")) in _LIVE


def _stage_verdict(open_items: list[dict]) -> str:
    if not open_items:
        return "ok"
    severities = {normalize_severity(i.get("severity")) for i in open_items}
    return "problem" if "critical" in severities else "attention"


def stage_summary(items: list[dict]) -> list[dict]:
    """Per-stage verdict used by the checkup screen: ok/attention/problem."""
    out = []
    for stage in STAGES:
        mine = [i for i in items
                if (i.get("stage") or i.get("category")) == stage and _is_live(i)]
        out.append({"stage": stage, "title": _STAGE_TITLES[stage],
                    "verdict": _stage_verdict(mine), "open_count": len(mine)})
    return out
