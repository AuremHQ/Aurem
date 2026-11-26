"""
core/parliament/parliament.py — 2026-09-08 Phase 3 god-class split.

The `Parliament` class only — now a thin composition of the 5
collaborators split into their own files (router, councils, ceo,
healer, breaker). Moved verbatim out of the single core/parliament.py
(zero logic change).

Wired into ONLY two places (per founder spec):
  • services/loop_engine.py::_do_execute  — per-file LLM patch
  • services/loop_engine.py::_do_verify   — heal LLM call

Prompt Mode, ORA, Codebase Health, and every other path stay exactly
as they are. This module is NEVER imported by chat.py,
orchestrator.py, or any non-loop router.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from ..observability import trace_llm
from . import llm_call as _llm_call_mod
from .breaker import _GLOBAL_BREAKER
from .ceo import CEO
from .councils import (
    CouncilA, CouncilB, CouncilC,
    _COUNCIL_A_PERSONA, _COUNCIL_B_PERSONAS, _COUNCIL_C_PERSONAS,
)
from .routing import TaskRouter
from .scoring import _strip_fences, _score_output, detect_output_type
from .self_heal import SelfHeal

logger = logging.getLogger("aurem-dev.parliament")

# Part XII group 1 (b3) — fire-and-forget log tasks must be HELD or
# the event loop may garbage-collect them mid-flight (and an
# insert_one failure would surface only as "Task exception was never
# retrieved"). The set holds each task until it finishes; the done
# callback drops it and logs a real failure instead of losing the
# trace event silently. Still non-blocking: nothing awaits these.
_LOG_TASKS: set[asyncio.Task] = set()


def _drop_log_task(task: asyncio.Task) -> None:
    _LOG_TASKS.discard(task)
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.debug("parliament_log event insert failed: %r", exc)


class Parliament:
    """Top-level orchestrator.  Instantiate this class then call
    `await instance.run(task, context)`."""

    def __init__(self, db=None):
        self.router    = TaskRouter()
        self.councils  = {"A": CouncilA(), "B": CouncilB(), "C": CouncilC()}
        self.ceo       = CEO()
        self.healer    = SelfHeal()
        self._db       = db
        self._breaker  = _GLOBAL_BREAKER

    # ── Public API ────────────────────────────────────────────────
    async def run(self, task: str, context: dict | None = None) -> dict:
        """Run a Council vote + CEO decision for a task.

        Returns: {status, output, winner, scores, ceo_picked,
                  reasoning, council, gateway_ms, trace_id,
                  circuit_breaker_state, circuit_breaker_fallback}
        """
        t0 = time.monotonic()
        ctx = dict(context or {})
        # GAP 4 — distributed tracing.
        trace_id = str(uuid.uuid4())[:8]
        ctx["parliament_trace_id"] = trace_id

        # Iter 212m-153 — top-level Langfuse span so every child LLM
        # observation (council members, CEO judge, self-heal, fallback)
        # rolls up into a single trace per Parliament.run().  Silent
        # no-op when Langfuse is disabled.
        parent_meta = {
            "trace_id":        trace_id,
            "task_type":       ctx.get("task_type"),
            "user_id":         ctx.get("user_id"),
            "tenant_id":       ctx.get("tenant_id"),
            "loop_session_id": ctx.get("loop_session_id"),
            "file_path":       ctx.get("file_path"),
        }
        with trace_llm(
            "parliament.run",
            input={"task_preview": (task or "")[:600]},
            metadata=parent_meta,
            as_type="chain",
        ) as parent_span:
            decision = await self._run_inner(task, ctx, t0, trace_id)
            try:
                parent_span.set_output({
                    "status":      decision.get("status"),
                    "winner":      decision.get("winner"),
                    "council":     decision.get("council"),
                    "ceo_picked":  decision.get("ceo_picked"),
                })
                parent_span.set_metadata({
                    "gateway_ms":               decision.get("gateway_ms"),
                    "ceo_temp_key":             decision.get("ceo_temp_key"),
                    "circuit_breaker_state":    decision.get("circuit_breaker_state"),
                    "circuit_breaker_fallback": decision.get("circuit_breaker_fallback"),
                })
            except Exception:
                pass
            return decision

    async def _run_inner(self, task: str, ctx: dict, t0: float,
                          trace_id: str) -> dict:
        """Inner pipeline kept separate from the Langfuse parent span
        wrapper so the existing logic stays untouched and testable."""
        council_name = self.router.route(task, ctx)
        ctx["council"] = council_name
        self._log_event("route", trace_id, ctx, {
            "council":   council_name,
            "task_type": ctx.get("task_type"),
            "task_preview": (task or "")[:120],
        })

        # Findings fix 2026-11-25 — single-model shortcut (swift/pro).
        # The context flag is set by LoopEngine when the user's mode
        # pill is swift/pro AND this is a code_fix first attempt (see
        # should_use_single_model in routing.py). One protected call
        # instead of a 3-member vote; maxx/retry never set the flag,
        # so the full Council path below is untouched for them.
        # Non-code tasks ignore the flag so TaskRouter B/C selection
        # is preserved.
        if (ctx.get("single_model") is True
                and ctx.get("task_type") == "code_fix"
                and council_name == "A"):
            self._log_event("single_model_swift_pro", trace_id, ctx, {
                "reason": "single_model",
            })
            return await self._fallback_single_call(
                task=task, context=ctx, trace_id=trace_id, started=t0,
                reason="single_model",
            )

        # GAP 1 — circuit breaker check.  If the upstream LLM is sick,
        # skip the council fan-out entirely and fall back to a single
        # protected LLM call.  This guarantees Loop Mode never fully
        # stops because of a transient provider issue.
        if not self._breaker.should_attempt():
            logger.warning(
                "[parliament %s] circuit breaker OPEN — using single-call "
                "fallback (trace=%s)", council_name, trace_id,
            )
            self._log_event("circuit_open_fallback", trace_id, ctx, {
                "state": self._breaker.state,
                "stats": self._breaker.stats(),
            })
            return await self._fallback_single_call(
                task=task, context=ctx, trace_id=trace_id, started=t0,
            )

        council = self.councils[council_name]
        member_temps = [m.temperature for m in council.members]
        self._log_event("council_start", trace_id, ctx, {
            "temps":       member_temps,
            "member_count": len(council.members),
            "circuit_state": self._breaker.state,
        })

        votes = await council.vote(task=task, context=ctx)
        self._log_event("council_done", trace_id, ctx, {
            "vote_count":  len(votes),
            "all_scores": [
                {"member": v["member"], "score": v["score"], "temp": v["temp"],
                 "error":  v.get("error")}
                for v in votes
            ],
        })

        decision = await self.ceo.decide(task=task, votes=votes, context=ctx)
        gateway_ms = round((time.monotonic() - t0) * 1000, 1)
        decision["council"]    = council_name
        decision["gateway_ms"] = gateway_ms
        decision["trace_id"]   = trace_id
        decision["circuit_breaker_state"]    = self._breaker.state
        decision["circuit_breaker_fallback"] = False
        decision["circuit_breaker_stats"]    = self._breaker.stats()

        self._log_event("ceo_decision", trace_id, ctx, {
            "status":         decision["status"],
            "winner":         decision.get("winner"),
            "ceo_picked":     decision.get("ceo_picked"),
            "ceo_temp_key":   decision.get("ceo_temp_key"),
            "ceo_temp_value": decision.get("ceo_temp_value"),
        })
        self._log_event("final", trace_id, ctx, {
            "status":      decision["status"],
            "duration_ms": gateway_ms,
            "council":     council_name,
        })
        # Aggregate row for analytics dashboards (kept for backward
        # compatibility with the original Iter 212m-150 schema).
        await self._log_aggregate(task=task, context=ctx,
                                  votes=votes, decision=decision,
                                  trace_id=trace_id)
        return decision

    # ── Fallback path when circuit breaker is OPEN (GAP 1) ────────
    # Also the deliberate single-model path (reason="single_model"):
    # same one call, but labelled as such — NOT a breaker fallback.
    async def _fallback_single_call(self, *, task: str, context: dict,
                                     trace_id: str, started: float,
                                     reason: str = "circuit_breaker_open") -> dict:
        """Single low-temperature LLM call to keep Loop Mode alive
        when the council fan-out would just multiply failures."""
        from services.loop_token_ledger import agent_call_context
        # Findings fix 2026-11-25 — thread the dynamic output budget
        # (Iter 362) into the single-model path too: a fixed 4000
        # would re-truncate the large files the budget fixed.
        _mto = context.get("max_tokens_override")
        if isinstance(_mto, int) and _mto > 0:
            _effective_max = min(32_000, max(4000, _mto))
        else:
            _effective_max = 4000
        # Part XII group 1 (b1) — the fallback used to hardcode the
        # Council A persona (and mode="code") even when the task was
        # routed to B/C, so an analysis/writing task was answered by
        # the code-fix persona. Pick the persona + LLM mode of the
        # council the router actually chose (context["council"], set
        # by _run_inner); the first member's persona stands in for
        # the council (B1 analyst / C1 direct copywriter).
        _council = (context.get("council") or "A").upper()
        if _council == "B":
            _persona, _mode = _COUNCIL_B_PERSONAS[0], "analysis"
        elif _council == "C":
            _persona, _mode = _COUNCIL_C_PERSONAS[0], "chat"
        else:
            _persona, _mode = _COUNCIL_A_PERSONA, "code"
        # Part XII group 1 (b2) — same hardcoding for the score: it
        # used task_type="code_fix" unconditionally. Thread the real
        # task_type through (default code_fix when absent).
        _task_type = context.get("task_type") or "code_fix"
        # CEO temp key follows the same routed council instead of
        # always claiming code_output.
        _temp_key = detect_output_type(task, council=_council)
        # 2026-08 hardening (F3) — separable from council/CEO calls.
        async with agent_call_context("single-model"):
            content, latency_ms, err = await _llm_call_mod._llm_call_protected(
                system=_persona, user=task,
                max_tokens=_effective_max, mode=_mode, review_mode="pro",
                user_id=context.get("user_id"),
                temperature=0.1,
                trace_name="parliament.fallback_single",
                trace_metadata={
                    "trace_id":   trace_id,
                    "reason":     reason,
                    "council":    context.get("council"),
                    "task_type":  context.get("task_type"),
                },
            )
        gateway_ms = round((time.monotonic() - started) * 1000, 1)
        if err or not content.strip():
            decision = {
                "status":                  "manual_review",
                "output":                  None,
                "winner":                  None,
                "scores":                  [],
                "ceo_picked":              False,
                "reasoning":               (f"Circuit-breaker fallback also failed: {err}"
                                           if reason == "circuit_breaker_open"
                                           else f"Single-model call also failed: {err}"),
                "council":                 context.get("council", "A"),
                "gateway_ms":              gateway_ms,
                "trace_id":                trace_id,
                "circuit_breaker_state":   self._breaker.state,
                "circuit_breaker_fallback": reason == "circuit_breaker_open",
                "single_model":             reason == "single_model",
                "circuit_breaker_stats":   self._breaker.stats(),
                "ceo_temp_key":            _temp_key,
                "ceo_temp_value":          0.0,
                **({"error_code": "COST_CAP_REACHED"} if err == "cost_cap_reached" else {}),
            }
        else:
            out = _strip_fences(content)
            decision = {
                "status":                  "success",
                "output":                  out,
                "winner":                  "fallback-single",
                "scores":                  [{
                    "member":  "fallback-single",
                    "score":   _score_output(out, task_type=_task_type,
                                             file_path=context.get("file_path")),
                    "temp":    0.1,
                    "len":     len(out),
                    "error":   None,
                }],
                "ceo_picked":              False,
                "reasoning":               ("Circuit-breaker fallback — "
                                           "council bypassed; single LLM call."
                                           if reason == "circuit_breaker_open"
                                           else "Single-model swift/pro — "
                                                "council bypassed; single LLM call."),
                "council":                 context.get("council", "A"),
                "gateway_ms":              gateway_ms,
                "trace_id":                trace_id,
                "circuit_breaker_state":   self._breaker.state,
                "circuit_breaker_fallback": reason == "circuit_breaker_open",
                "single_model":             reason == "single_model",
                "circuit_breaker_stats":   self._breaker.stats(),
                "ceo_temp_key":            _temp_key,
                "ceo_temp_value":          0.0,
            }
        self._log_event("final", trace_id, context, {
            "status":                  decision["status"],
            "duration_ms":             gateway_ms,
            "circuit_breaker_fallback": decision["circuit_breaker_fallback"],
            "single_model":             decision.get("single_model", False),
        })
        await self._log_aggregate(task=task, context=context, votes=[],
                                  decision=decision, trace_id=trace_id)
        return decision

    # ── Logging helpers (GAP 4) ───────────────────────────────────
    def _log_event(self, event: str, trace_id: str,
                   context: dict, data: dict) -> None:
        """Non-blocking distributed-trace event.  Fire-and-forget so
        Mongo latency never blocks the LLM pipeline."""
        if self._db is None:
            return
        entry = {
            "trace_id":        trace_id,
            "event":           event,
            "data":            data,
            "ts":              time.time(),
            "loop_session_id": context.get("loop_session_id"),
            "user_id":         context.get("user_id"),
            "tenant_id":       context.get("tenant_id"),
            "file_path":       context.get("file_path"),
            "council":         context.get("council"),
        }
        try:
            _task = asyncio.create_task(self._db.parliament_log.insert_one(entry))
        except Exception as e:                             # noqa: BLE001
            logger.debug("parliament_log event=%s insert failed: %r",
                         event, e)
            return
        # Part XII group 1 (b3) — hold the task so it cannot be
        # garbage-collected mid-flight; the done callback drops it
        # and logs an insert failure (the old code kept no reference
        # at all, so a failed insert surfaced at most as "Task
        # exception was never retrieved"). Still non-blocking.
        _LOG_TASKS.add(_task)
        _task.add_done_callback(_drop_log_task)

    async def _log_aggregate(self, *, task: str, context: dict,
                              votes: list[dict], decision: dict,
                              trace_id: str) -> None:
        """The single roll-up row callers like analytics dashboards
        can scan without re-assembling individual trace events."""
        if self._db is None:
            return
        try:
            await self._db.parliament_log.insert_one({
                "event":           "aggregate",
                "trace_id":        trace_id,
                "loop_session_id": context.get("loop_session_id"),
                "user_id":         context.get("user_id"),
                "tenant_id":       context.get("tenant_id"),
                "file_path":       context.get("file_path"),
                "council":         decision.get("council") or context.get("council"),
                "task_type":       context.get("task_type"),
                "task_preview":    (task or "")[:200],
                "status":          decision.get("status"),
                "winner":          decision.get("winner"),
                "scores":          decision.get("scores"),
                "ceo_picked":      decision.get("ceo_picked"),
                "reasoning":       decision.get("reasoning"),
                "gateway_ms":      decision.get("gateway_ms"),
                "ceo_temp_key":    decision.get("ceo_temp_key"),
                "ceo_temp_value":  decision.get("ceo_temp_value"),
                "circuit_breaker_state":     decision.get("circuit_breaker_state"),
                "circuit_breaker_fallback":  decision.get("circuit_breaker_fallback"),
                "ts":              time.time(),
            })
        except Exception as e:                             # noqa: BLE001
            logger.debug("parliament_log aggregate insert failed: %r", e)
