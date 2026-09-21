"""
services/self_bug.py — P7 SELF-REPAIR (2026-08-31)

ORA recognizing its OWN bugs (not the user's website) — detect,
diagnose, log, learn. Honest scope: this module NEVER writes to
ORA's own deployed code. It only logs a structured bug report + an
optional PROPOSED patch (a string) for a human/CI to review — the
same PR-only + human-approve standard used for every user repo
(see services/local_tools.py write paths). There is no unattended
self-apply path here, by construction: `emit()`/`diagnose()` are
read/log-only.

Same determinism discipline as the other new guard modules in this
rework — no LLM in detection/classification; `diagnose()` only ever
states a cause that is actually IN `_KNOWN_CAUSES`, never invents one
to sound confident (no-false-claims).

2026-09-21 — Self-Repair audit (Part 2.1/3/4):
  * Part 2.1 fix — `signature()` used to collapse to `f"{type}:{type}"`
    for every caller (none ever passed `context["subject"]`), so every
    row of the same bug TYPE shared one identical `times_seen`/
    `last_seen` in the admin dashboard regardless of what the actual
    reply said. Now derives a subject from the evidence text when the
    caller doesn't supply one.
  * Part 3 — `self_bug_learned` now tracks `first_seen`/`status`/
    `fixed_on`/`fixed_by`; see `mark_fixed()`.
  * Part 4 — `propose_fix()` is PROPOSE-ONLY (see its own docstring).
    No auto-apply path exists anywhere in this codebase.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from pymongo import ReturnDocument

from cto_services.db import get_db

logger = logging.getLogger(__name__)

SELF_BUG_TYPES = frozenset({
    "missing_button", "truncated_reply", "dead_end_leak", "jargon_leak",
    "tool_error", "stalled_silence", "blank_ui", "user_reported",
})

# type -> the ONLY cause we ever state for it. Anything not backed by
# a row here comes back as confidence="uncertain" — never a guess.
_KNOWN_CAUSES = {
    "missing_button": "the Approve-the-fix button's rendering gate (extractHandoffBrief) didn't find a valid fence in the reply",
    "truncated_reply": "the reply ended before a complete sentence — the completeness guard caught it",
    "dead_end_leak": "the reply contained a banned dead-end phrase (try rephrasing / not confident) before the guard rewrote it",
    "jargon_leak": "the reply contained a raw filename or dev term before the voice filter caught it",
    "tool_error": "a tool call ORA made returned an error",
    "stalled_silence": "no visible progress was emitted for an extended period during tool work",
    "blank_ui": "a UI panel rendered with no content",
    "user_reported": "the user described ORA's own behavior (not their website) as broken",
}
_HIGH_SEVERITY_TYPES = frozenset({"missing_button", "tool_error", "user_reported"})

# 2026-02 — Gap 1 fix: `tool_error`'s single generic cause above
# ("a tool call ORA made returned an error") was TRUE but imprecise
# for the two concrete patterns this round wires up detection for —
# both originate from a tool call (read_repo_file's not-found path;
# a repo-edit/handoff turn) but the user-visible fault isn't "the
# tool errored", it's "the guard around that tool's output let
# something unsafe through". Per the no-invented-cause rule, these
# two extra causes are only used when `source` is EXACTLY one of the
# two real guard modules that now call self_bug.emit() for
# `tool_error` (services/chat_helpers.py:apply_output_guards) —
# anything else still falls back to the generic cause above.
_SOURCE_CAUSES = {
    ("tool_error", "internal_directive_guard"):
        "a tool call (read_repo_file) failed and its raw internal "
        "retry instruction ('STOP guessing paths...') leaked into "
        "the user-facing reply instead of being stripped",
    ("tool_error", "handoff_diff_safety_guard"):
        "a stray fenced code block next to an aurem-handoff brief "
        "rendered as a live file diff before Approve was clicked, "
        "instead of being stripped as scope creep",
}


@dataclass
class SelfBugEvent:
    type: str
    source: str
    evidence: str
    context: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class Diagnosis:
    what_user_saw: str
    what_ora_detected: str
    likely_cause: str
    evidence: str
    confidence: str  # "confirmed" | "likely" | "uncertain"
    proposed_fix: Optional[str] = None
    patch: Optional[str] = None
    severity: str = "low"


def diagnose(event: SelfBugEvent) -> Diagnosis:
    """Honest, no-invented-cause diagnosis. Only states a cause this
    module actually KNOWS from `event.type` — never guesses beyond
    that, and always frames the fault as ORA's own, never the user's
    website (see services/self_bug_reply_guard.py for the reply that
    uses this)."""
    if event.type not in SELF_BUG_TYPES:
        return Diagnosis(
            what_user_saw=event.evidence or "something didn't work as expected",
            what_ora_detected="an unrecognized signal",
            likely_cause="uncertain — no known-cause mapping for this event type",
            evidence=event.evidence or "",
            confidence="uncertain",
        )
    cause = _SOURCE_CAUSES.get((event.type, event.source), _KNOWN_CAUSES[event.type])
    confidence = "confirmed" if event.evidence else "likely"
    severity = "high" if event.type in _HIGH_SEVERITY_TYPES else "low"
    return Diagnosis(
        what_user_saw=event.evidence or f"a {event.type.replace('_', ' ')} issue",
        what_ora_detected=f"{event.source} flagged a {event.type.replace('_', ' ')}",
        likely_cause=cause,
        evidence=event.evidence or "",
        confidence=confidence,
        severity=severity,
    )


def signature(event_type: str, context: dict | None = None, evidence: str = "") -> str:
    """Deterministic recurrence key — the SAME kind of self-bug always
    maps to the same signature regardless of exact wording, so the
    team sees 'this bug, 7 times' instead of 7 fresh diagnoses.

    2026-09-21 fix (Part 2.1): no caller ever populated
    `context["subject"]`, so this collapsed to `f"{type}:{type}"` for
    EVERY event of a given type — which is why the admin dashboard
    showed the identical times_seen/last_seen on every row regardless
    of the actual reply content. When no explicit subject is given,
    derive one from a normalized snippet of `evidence` instead, so
    genuinely-different occurrences of the same bug type get distinct
    signatures, while truly-repeated identical phrasing still
    correctly collapses onto one (that collapsing IS the intended
    recurrence signal — just at the right granularity now)."""
    subject = (context or {}).get("subject")
    if not subject:
        norm = re.sub(r"\s+", " ", (evidence or "").strip().lower())
        subject = norm[:80] if norm else event_type
    return f"{event_type}:{subject}"


async def emit(event_type: str, evidence: str = "", context: dict | None = None,
                source: str = "guard") -> Optional[SelfBugEvent]:
    """Best-effort, never raises — a self-bug LOGGING failure must
    never break the real chat turn it's reporting on."""
    if event_type not in SELF_BUG_TYPES:
        logger.warning("self_bug.emit: unknown type %r", event_type)
        return None
    event = SelfBugEvent(type=event_type, source=source, evidence=evidence or "",
                         context=context or {})
    diagnosis = diagnose(event)
    db = get_db()
    if db is None:
        return event
    try:
        sig = signature(event.type, event.context, event.evidence)
        await db.ora_self_bugs.insert_one({
            "type": event.type,
            "source": event.source,
            "evidence": event.evidence,
            "context": event.context,
            "what_user_saw": diagnosis.what_user_saw,
            "what_ora_detected": diagnosis.what_ora_detected,
            "likely_cause": diagnosis.likely_cause,
            "confidence": diagnosis.confidence,
            "severity": diagnosis.severity,
            "proposed_fix": diagnosis.proposed_fix,
            "signature": sig,
            "ts": event.ts,
        })
        times_seen = await _record_recurrence(db, sig)
        # Part 4 — PROPOSE-ONLY fix suggestion once recurrence crosses
        # the threshold. See propose_fix()'s docstring: this only ever
        # writes a text field for a human to read on the admin
        # dashboard — nothing consumes it automatically.
        proposal = propose_fix(event.type, times_seen, "open")
        if proposal:
            await db.self_bug_learned.update_one(
                {"signature": sig},
                {"$set": {
                    "proposed_fix_text": proposal,
                    "proposed_fix_generated_at": time.time(),
                    "proposed_fix_review_status": "pending",
                }},
            )
    except Exception as e:  # pragma: no cover
        logger.warning("self_bug.emit insert failed: %r", e)
    return event


async def _record_recurrence(db, sig: str) -> int:
    """Upserts the learned-pattern store. Returns the new times_seen.
    Real learning here = pattern-recognition + a recurrence counter,
    NOT model weight updates — matches the honest scope in the
    module docstring.

    2026-09-21 Part 3 — also tracks `first_seen` (set once, on
    insert) and a `status` field (open/fixed) so the admin dashboard
    can show first-seen/last-seen/open-vs-fixed instead of just a
    raw count."""
    doc = await db.self_bug_learned.find_one_and_update(
        {"signature": sig},
        {
            "$inc": {"times_seen": 1},
            "$set": {"last_seen": time.time()},
            "$setOnInsert": {
                "first_seen": time.time(), "status": "open",
                "fixed_on": None, "fixed_by": None,
            },
        },
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    return (doc or {}).get("times_seen", 1)


async def mark_fixed(sig: str, status: str = "fixed",
                      fixed_by: Optional[str] = None) -> bool:
    """Part 3 — manual fix-status action. No automatic deploy/commit
    linkage exists yet (would need a deploy-webhook -> signature
    mapping that isn't built), so this is the documented manual
    fallback: an admin marks a signature fixed/re-opened by hand,
    timestamped with today's date. Returns True if a matching
    learned-pattern row existed to update."""
    if status not in ("fixed", "open"):
        raise ValueError("status must be fixed|open")
    db = get_db()
    if db is None:
        return False
    update = {"status": status}
    if status == "fixed":
        update["fixed_on"] = time.time()
        update["fixed_by"] = fixed_by
    else:
        update["fixed_on"] = None
        update["fixed_by"] = None
    r = await db.self_bug_learned.update_one({"signature": sig}, {"$set": update})
    return r.matched_count > 0


# ── Part 4 — PROPOSE-ONLY fix suggestions (2026-09-21) ────────────────
# Deterministic, template-based — same no-LLM-in-detection discipline
# as the rest of this module. ONE template per bug type; anything not
# in this dict returns None (no invented suggestion).
_PROPOSED_FIX_RECURRENCE_THRESHOLD = 5

_FIX_TEMPLATES = {
    "missing_button": "Check extractHandoffBrief's fence-detection regex against the exact reply shape in `evidence` — it isn't recognizing a valid ```aurem-handoff fence here.",
    "truncated_reply": "Review incomplete_reply_guard's completeness heuristic against the exact cut-off point in `evidence` — it may need a new terminal-punctuation/length exception for this shape.",
    "dead_end_leak": "This reply is a genuine dead end (already passed the correct-refusal check), not a false positive — review the system prompt for this task type and consider adding an explicit 'always offer a concrete alternative' reminder for it.",
    "jargon_leak": "Extend business_voice_filter's jargon term list with the specific term(s) visible in `evidence`.",
    "tool_error": "Check the tool named in `evidence`/`context` for a recurring failure mode (bad args shape, auth, timeout) rather than a one-off.",
    "stalled_silence": "Check whether the tool sequence in `context` is missing a progress/activity_hook call during a long-running step.",
    "blank_ui": "Check the UI panel referenced in `evidence` for a render-gate that's failing closed.",
    "user_reported": "No generic template applies to a free-text user report — read `evidence` directly.",
}


def propose_fix(event_type: str, times_seen: int, status: str) -> Optional[str]:
    """Part 4 — PROPOSE-ONLY. Returns a deterministic, template-based
    fix suggestion string once a bug has recurred
    `_PROPOSED_FIX_RECURRENCE_THRESHOLD`+ times while still
    `status == "open"`. Returns None otherwise — including anything
    already marked fixed, so a stale proposal never lingers on a
    resolved row, and anything below the recurrence threshold.

    Because `emit()` (the only writer of `ora_self_bugs`) now only
    fires `dead_end_leak` for replies that already passed the
    Part 2.2 correct-refusal check (see services/legitimate_refusal.py
    and no_dead_end_guard.has_dead_end()), anything reaching this
    function is, by construction, not a correct-refusal false
    positive.

    CRITICAL — this function ONLY returns text for a human to read on
    the admin dashboard. Nothing in this codebase applies this text
    automatically. Auto-apply is explicitly OUT OF SCOPE here: per
    spec, it would require a SEPARATE, later, per-bug-type opt-in
    after a human has manually approved a handful of proposals for
    that exact type first — no such opt-in switch or auto-apply
    mechanism is built anywhere in this codebase."""
    if status != "open" or times_seen < _PROPOSED_FIX_RECURRENCE_THRESHOLD:
        return None
    return _FIX_TEMPLATES.get(event_type)


async def known_handling(sig: str) -> Optional[dict]:
    """If this signature has been seen before, return its learned
    record (times_seen/last_seen) so the caller can skip re-diagnosis
    and go straight to the known ownership+path-forward reply."""
    db = get_db()
    if db is None:
        return None
    try:
        return await db.self_bug_learned.find_one({"signature": sig}, {"_id": 0})
    except Exception as e:  # pragma: no cover
        logger.warning("self_bug.known_handling lookup failed: %r", e)
        return None


async def self_bug_open(session_id: str, window_seconds: float = 30.0) -> bool:
    """True if a self-bug was logged for this session within the last
    `window_seconds` — signals the CURRENT turn's reply should follow
    the self-bug reply pattern (ownership + path-forward, never
    blaming the user — see services/self_bug_reply_guard.py)."""
    if not session_id:
        return False
    db = get_db()
    if db is None:
        return False
    try:
        cutoff = time.time() - window_seconds
        doc = await db.ora_self_bugs.find_one(
            {"context.session_id": session_id, "ts": {"$gte": cutoff}},
            sort=[("ts", -1)],
        )
        return doc is not None
    except Exception as e:  # pragma: no cover
        logger.warning("self_bug.self_bug_open lookup failed: %r", e)
        return False
