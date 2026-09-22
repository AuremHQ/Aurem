"""
services/semantic_mode_router.py — 2026-09 P0 bug-brief, Bug 2.

LIVE-REPRODUCED root cause (not assumed from a comment): worker.py's
`_mode_broadcast()` calls `chat_helpers.classify_intent()` — a pure
regex/substring priority list — as the SOLE dispatch decision for
Mode D (debug) / Mode E (audit). `services.mode_classifier`'s
confidence score already exists and is broadcast to the frontend as
a `needs_confirm` hint, but nothing in the backend actually GATES
dispatch on it — `_mode_d_e()` runs the picked mode's real pipeline
(`run_debug_session` / `run_audit`) unconditionally, regardless of
confidence. Confirmed live via `classify_intent()` directly:

    "That last message failed to deliver (Cloudflare origin error).
     Retrying: please audit the repo for X and recommend Y."
    + a REAL captured console error (plausible when a message
      genuinely failed to send) -> "D"

A message whose actual ask is "audit + recommend" gets hijacked into
the expensive Mode D debug pipeline purely because the SOFT_DEBUG /
ENV_SIGNAL_REFERENCE regexes matched the word "error" in an unrelated
transport-layer complaint.

This module is a FEATURE-FLAGGED (`SEMANTIC_MODE_ROUTER_ENABLED`,
default OFF) alternative dispatch path. When OFF, callers must get
byte-identical behaviour to today — `classify_mode_semantic()` just
returns the existing keyword classifier's answer.

2026-09-22 — canary rollout (founder-approved after live-proving Jev,
see chat log). `SEMANTIC_MODE_ROUTER_CANARY_PCT` (default 100, i.e.
"everyone" once the master flag is ON — this keeps the pre-canary
test suite's assumptions valid) lets a SLICE of sessions run the
semantic path while everyone else still gets byte-identical keyword
behaviour, even with the master flag ON. Sampling is deterministic
per session_id (stable hash % 100), so a given conversation never
flips buckets mid-thread. Rollback is one env var: set the pct back
to 0, or the master flag back to 0 — no deploy needed either way.
Observability: every classification (in-canary or not) already logs
to `mode_classifications` via `services.mode_classifier.log_classification`
(see worker.py's `_mode_broadcast`), and `router_source` in that same
event distinguishes "jev" (in-canary) from "keyword" (everyone else)
— so the canary's real-traffic behaviour is visible without grepping
logs. When ON:

  1. Ask Jev (services.llm.jev_client — see that module's docstring;
     already integrated 2026-09-19, live-verified again this round
     with a real OpenRouter Decisions API call) to pick ONE of the
     6 modes given the FULL message, not a keyword scan.
  2. Confidence < MODE_ROUTER_CONFIDENCE_THRESHOLD (default 0.72):
     never switch off the session's current mode. This is precisely
     the guard the keyword router lacks — a bare "error"/"audit"
     mention in an otherwise-unrelated message should not clear this
     bar.
  3. Hysteresis: even a high-confidence signal for a NEW mode must
     repeat on 2 consecutive turns before the session actually
     switches — one ambiguous message can never flip it.
  4. Any failure (Jev unreachable, malformed answer, timeout) falls
     back to the existing keyword classifier for THAT turn only — a
     classifier outage must never block a chat turn, and never
     corrupts the session's hysteresis state.

Session mode history is tracked in-process (module-level, LRU-capped
dict keyed by session_id) — a deliberate v1 simplification. This
resets on a backend restart/redeploy or drifts across multiple pod
replicas without a shared store. Acceptable for v1 because (a) the
flag defaults OFF, (b) the worst case matches today's behaviour
exactly (no user-visible regression), and (c) swapping in a
Mongo-backed store is a mechanical follow-up once this is validated
on real traffic.
"""
from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import Optional

from services.chat_helpers import classify_intent as _keyword_classify_intent

logger = logging.getLogger(__name__)

_MODES = ("A", "B", "C", "D", "E", "F")

_MODE_LABELS = {
    "A": (
        "general conversation, explanation, casual chat, or a "
        "greeting — no repo edit, no debugging, no audit being asked"
    ),
    "B": (
        "decision support — the user is torn between options and "
        "wants advice, comparison, or a recommendation, not code work"
    ),
    "C": (
        "agentic code change — build, ship, commit, push, deploy, "
        "or fix something IN the repository right now"
    ),
    "D": (
        "debug — the user is asking to diagnose a REAL error/bug/"
        "crash that is happening in THEIR app right now. A message "
        "that only MENTIONS an error in passing (e.g. describing a "
        "chat-transport hiccup) while actually asking for something "
        "else does NOT belong here"
    ),
    "E": (
        "audit — the user wants a review/analysis of the repo, a "
        "specific system, or existing logic, with findings and/or "
        "a recommendation"
    ),
    "F": (
        "engage — marketing copy, positioning, GTM, competitor "
        "comparison, or promotional writing, not code work"
    ),
}

_CONFIDENCE_THRESHOLD = float(os.getenv("MODE_ROUTER_CONFIDENCE_THRESHOLD", "0.72"))
_HYSTERESIS_STREAK_REQUIRED = 2
_SESSION_STATE_MAX = 5000

# session_id -> {"current_mode": str|None, "pending_mode": str|None, "streak": int}
_SESSION_STATE: "OrderedDict[str, dict]" = OrderedDict()


def is_enabled() -> bool:
    return os.getenv("SEMANTIC_MODE_ROUTER_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def _canary_pct() -> int:
    try:
        pct = int(os.getenv("SEMANTIC_MODE_ROUTER_CANARY_PCT", "100"))
    except ValueError:
        pct = 100
    return max(0, min(100, pct))


def _in_canary(session_id: str) -> bool:
    """Deterministic per-session sampling — same session always lands
    in the same bucket for its whole lifetime, so hysteresis state
    never straddles the canary boundary mid-conversation."""
    pct = _canary_pct()
    if pct >= 100:
        return True
    if pct <= 0:
        return False
    import hashlib
    digest = hashlib.sha256((session_id or "").encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 100) < pct


def _get_session_state(session_id: str) -> dict:
    st = _SESSION_STATE.get(session_id)
    if st is None:
        st = {"current_mode": None, "pending_mode": None, "streak": 0}
        _SESSION_STATE[session_id] = st
        if len(_SESSION_STATE) > _SESSION_STATE_MAX:
            _SESSION_STATE.popitem(last=False)
    else:
        _SESSION_STATE.move_to_end(session_id)
    return st


async def _ask_jev_for_mode(message: str) -> Optional[dict]:
    """Real Jev Decisions API call. Returns {"mode", "confidence"} or
    None on any failure — mirrors services/ora_chat/intent_router.py's
    classify_intent_jev() calling convention exactly."""
    from services.llm.jev_client import ask_choice
    try:
        label, meta = await ask_choice(
            state=f"Chat message to a coding assistant: {(message or '')[:4000]!r}",
            instructions=(
                "Classify the SINGLE best-fit category for this message, "
                "based on its actual intent, not just whether it contains "
                "a particular word."
            ),
            criteria=_MODE_LABELS,
        )
    except Exception as e:  # noqa: BLE001 — classifier outage must never raise
        logger.warning("semantic_mode_router: Jev call raised — %r", e)
        return None
    if label not in _MODES:
        if label is not None:
            logger.warning("semantic_mode_router: unexpected Jev label %r", label)
        return None
    confidence = float((meta.get("probabilities") or {}).get(label, 0.0))
    return {"mode": label, "confidence": confidence}


async def classify_mode_semantic(
    message: str,
    f12_payload: Optional[dict],
    project_id: Optional[str],
    session_id: Optional[str],
) -> dict:
    """Returns {"mode": <A-F>, "confidence": float|None, "source":
    "jev"|"keyword"|"keyword_fallback", "switched": bool}.

    NEVER raises. When the flag is off, or Jev fails, degrades to the
    existing `classify_intent()` — byte-identical to pre-2026-09
    behaviour."""
    fallback_mode = _keyword_classify_intent(message, f12_payload, project_id)
    if not is_enabled():
        return {"mode": fallback_mode, "confidence": None, "source": "keyword", "switched": True}
    if not _in_canary(session_id or ""):
        # Outside the canary slice — byte-identical to flag-off, even
        # though the master flag is ON. No hysteresis state touched.
        return {"mode": fallback_mode, "confidence": None, "source": "keyword", "switched": True}

    st = _get_session_state(session_id or "")
    current = st["current_mode"]

    jev_result = await _ask_jev_for_mode(message)
    if jev_result is None:
        chosen = current if current else fallback_mode
        return {
            "mode": chosen, "confidence": None,
            "source": "keyword_fallback", "switched": chosen != current,
        }

    candidate, confidence = jev_result["mode"], jev_result["confidence"]

    if current is None:
        # First turn in this session. Still respect the confidence bar
        # — a shaky first signal defaults to "A" rather than firing an
        # expensive D/E pipeline on a guess.
        chosen = candidate if confidence >= _CONFIDENCE_THRESHOLD else "A"
        st["current_mode"], st["pending_mode"], st["streak"] = chosen, None, 0
        return {"mode": chosen, "confidence": confidence, "source": "jev", "switched": True}

    if candidate == current:
        st["pending_mode"], st["streak"] = None, 0
        return {"mode": current, "confidence": confidence, "source": "jev", "switched": False}

    if confidence < _CONFIDENCE_THRESHOLD:
        # Below the bar — this is the exact guard the keyword router
        # lacked. Never switch off one ambiguous signal.
        return {"mode": current, "confidence": confidence, "source": "jev", "switched": False}

    # High-confidence signal for a DIFFERENT mode — hysteresis: must
    # repeat next turn too before we actually switch.
    if st["pending_mode"] == candidate:
        st["streak"] += 1
    else:
        st["pending_mode"], st["streak"] = candidate, 1

    if st["streak"] >= _HYSTERESIS_STREAK_REQUIRED:
        st["current_mode"], st["pending_mode"], st["streak"] = candidate, None, 0
        return {"mode": candidate, "confidence": confidence, "source": "jev", "switched": True}

    return {"mode": current, "confidence": confidence, "source": "jev", "switched": False}
