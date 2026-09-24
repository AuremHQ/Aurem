"""
routers/chat/watchdog.py — SSE queue-consumption + soft/hard timeout
race, extracted out of chat_stream()'s gen() (2026-09-08 StreamState
refactor). Mechanical move — the exact original while-loop body,
now reading/writing StreamState fields instead of closure variables.
See stream_state.py's docstring for the field mapping.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as _t

from .stream_state import StreamState

logger = logging.getLogger(__name__)


def _timeout_interaction(state: StreamState, content: str, partial_invocations: list,
                          ic_failure_mode: "str | None") -> dict:
    """INTERACTION_CONTRACT_V1 P1 — builds the same envelope shape
    `finalize()` would, but pinned to THIS turn's own
    `state.interaction_request_id` (the timeout path previously called
    `_persist_turn` with `interaction=None`, which made it mint a
    brand-new, unrelated request_id internally — harmless before the
    ledger existed, but would have silently broken retry-replay
    identity now that a retry looks up a record BY that id)."""
    from services.interaction_contract import finalize
    turn_state = "partial" if partial_invocations else "error"
    return finalize(
        {"turn_state": turn_state, "failure_mode": ic_failure_mode},
        content, prompt=state.body.prompt or "", session_id=state.body.session_id or "",
        request_id=state.interaction_request_id,
    )


async def consume_worker_queue(state: StreamState, worker_t, ticker_t):
    """Async generator yielding SSE `data: ...\\n\\n` lines. On a
    clean `result`/`error` event it sets `state.result` /
    `state.collected_steps` and returns normally (caller continues
    post-processing). On a blown deadline it fully completes the SSE
    stream itself (meta → tokens → persist → done), sets
    `state.timed_out = True`, and returns — caller must check that
    flag and stop immediately without further processing.
    """
    from services.chat_helpers import _persist_turn
    from services.interaction_contract import enabled as _ic_enabled

    q = state.q
    activity = state.activity
    t_start = state.t_start
    HARD_TIMEOUT_S = state.hard_timeout_s
    SOFT_TIMEOUT_S = state.soft_timeout_s
    body = state.body

    result = None
    collected_steps: list = []
    deadline_at = _t.monotonic() + HARD_TIMEOUT_S
    while True:
        try:
            ev = await asyncio.wait_for(
                q.get(), timeout=max(0.1, deadline_at - _t.monotonic()),
            )
        except asyncio.TimeoutError:
            ev = None  # synthetic timeout — handled below
        # Iter 136 — explicit deadline check.
        # The `_ticker()` task fires every 0.6s and feeds the queue, so
        # wait_for(q.get(), ...) almost always returns before the
        # configured timeout. Result: HARD_TIMEOUT_S was never being
        # enforced — users saw "thinking · 500s" past the 150s budget.
        # Now we treat ANY tick that arrives past the deadline as a
        # timeout, but DON'T throw away a real `result` / `mode` / `error`
        # event just because it raced past the cut-off by a few ms.
        _past_deadline = _t.monotonic() >= deadline_at
        _is_tick = isinstance(ev, dict) and ev.get("type") == "tick"
        # 2026-08-23 audit fix — see SOFT_TIMEOUT_S comment above.
        _tool_count_so_far = len(activity.get("invocations") or [])
        _past_soft_deadline = (
            _t.monotonic() >= (t_start + SOFT_TIMEOUT_S)
            and _tool_count_so_far <= 1
        )
        # INTERACTION_CONTRACT_V1 P1 — 4-clock tracker (see
        # services/timeout_clocks.py). Only active when the flag is ON
        # AND stream.py actually attached one to this state (both
        # true whenever `_ic_enabled()` was true at gen()-start).
        # `evaluate()` can only fire EARLIER than or AT the same
        # instant as the legacy hard/soft pair above (its own
        # `hard_deadline_s` mirrors HARD_TIMEOUT_S exactly) — it never
        # extends a turn's runtime past what legacy behavior already
        # allowed.
        _clock_breach = None
        if state.timeout_clocks is not None and _is_tick:
            _clock_breach = state.timeout_clocks.evaluate()
        _ic_timeout = _clock_breach is not None
        if ev is None or (_past_deadline and _is_tick) or (_past_soft_deadline and _is_tick) or (_ic_timeout and _is_tick):
            # Wall-clock blown. Cancel everything but emit a USEFUL
            # message instead of just an "error" payload — the
            # frontend used to render that red and the user saw
            # nothing actionable. We pull whatever tool history the
            # worker managed to record and stream a real summary.
            worker_t.cancel()
            ticker_t.cancel()
            partial_invocations = list(activity.get("invocations") or [])
            from services.orchestrator import _synthesise_max_iters_summary
            summary = _synthesise_max_iters_summary(
                body.prompt, partial_invocations,
            )
            # RECURRING_ISSUES.md Pattern #2 fix: distinguish slow-API
            # waiting from a genuine reasoning loop. If we made very
            # few tool calls, the time was likely spent waiting on
            # the model API (cold start / OpenRouter queue / network),
            # NOT looping. Telling the user "I cut myself off" in
            # that case is misleading and erodes trust.
            # 2026-08-19 — extracted to orchestrator.build_timeout_message
            # so this decision is directly unit-testable (the old
            # grep-lock test couldn't catch a swapped branch).
            tool_count = len(partial_invocations)
            from services.orchestrator import build_timeout_message
            # 2026-08-23 audit fix — report whichever budget actually
            # fired so the message says "48s" not a misleading "180s"
            # when the soft (proxy-safe) deadline is what triggered.
            # INTERACTION_CONTRACT_V1 P1 — when one of the 4 named
            # clocks fired, its own budget/name takes precedence over
            # the legacy soft/hard labels for an accurate message.
            _ic_clock_name = _clock_breach[0] if _clock_breach else None
            _ic_failure_mode = _clock_breach[1] if _clock_breach else None
            if _ic_clock_name:
                _effective_budget = state.timeout_clocks.budget_for(_ic_clock_name)
            else:
                _effective_budget = (
                    SOFT_TIMEOUT_S if (_past_soft_deadline and not _past_deadline)
                    else HARD_TIMEOUT_S
                )
            content, _slow_api = build_timeout_message(
                tool_count, _effective_budget, summary,
            )
            # Stream as a normal assistant turn (meta → tokens → done)
            # so the bubble renders properly instead of going red.
            meta_payload = {
                "meta": True,
                "session_id": body.session_id,
                "provider": "aurem-timeout-guard",
                "mode": "A",
                "temperature": 0.2,
                "thinking_s": round(_t.monotonic() - t_start, 1),
                "tool_calls_run": len(partial_invocations),
                "timed_out": True,
                "slow_api": _slow_api,
            }
            yield f"data: {json.dumps(meta_payload)}\n\n"
            CHUNK = 16
            for i in range(0, len(content), CHUNK):
                yield f"data: {json.dumps({'token': content[i:i+CHUNK]})}\n\n"
                await asyncio.sleep(0.005)
            # Persist the turn so refresh keeps it visible.
            # INTERACTION_CONTRACT_V1 P1 — carry the SAME request_id
            # stream.py minted so a future retry's `lookup_retry()`
            # and this turn's own persisted record share one identity,
            # and mark `stream_state="cancelled"` — WE (the timeout
            # guard) deliberately tore this down, the outcome is known.
            try:
                await _persist_turn(
                    state.user_id, body.session_id or "",
                    body.prompt, content, "aurem-timeout-guard",
                    project_id=body.project_id,
                    steps=collected_steps,
                    stream_state="cancelled" if _ic_enabled() else None,
                    interaction=(
                        _timeout_interaction(state, content, partial_invocations,
                                              _ic_failure_mode)
                        if _ic_enabled() else None
                    ),
                )
            except Exception:
                logger.exception("timeout persist_turn failed")
            if _ic_enabled():
                try:
                    from services.chat_reliability import record_cancelled
                    from cto_services.db import get_db as _get_db_ic
                    await record_cancelled(
                        _get_db_ic(), request_id=state.interaction_request_id,
                        reason=_ic_failure_mode or "hard_deadline_exceeded",
                        partial_content=content,
                        tool_calls_run=len(partial_invocations),
                    )
                except Exception:
                    logger.debug("chat_reliability record_cancelled skipped (watchdog timeout)")
            # 2026-09-08 — task-chain logging (structured, not raw CoT).
            try:
                from cto_services.db import get_db
                from services.task_chain_logging import log_task_chain
                await log_task_chain(
                    get_db(), user_id=state.user_id, session_id=body.session_id or "",
                    project_id=body.project_id, user_prompt=body.prompt or "",
                    tool_calls=partial_invocations or [], final_response=content,
                    turn_state="partial" if partial_invocations else "error",
                    failure_mode=None if partial_invocations else "provider_rate_or_5xx",
                )
            except Exception:
                logger.debug("task_chain logging skipped (watchdog timeout)")
            yield (
                "data: " + json.dumps({
                    "done": True,
                    "provider": "aurem-timeout-guard",
                    "session_id": body.session_id,
                    "tokens_remaining": None,
                    "timed_out": True,
                    # 2026-09-08 — Turn-Completion Contract. A timeout
                    # with real partial tool progress is an EXPLICIT
                    # PARTIAL (Continue is a working action — the next
                    # turn resumes with that history in context); a
                    # timeout with zero progress is an EXPLICIT
                    # FAILURE (nothing to continue, only retry makes
                    # sense). Never a bare "done" with no state.
                    "turn_state": "partial" if partial_invocations else "error",
                    "continue_available": bool(partial_invocations),
                    "error": None if partial_invocations else "no progress before timeout",
                    # INTERACTION_CONTRACT_V1 P1 — a fired 4-clock name
                    # (connect/first_token/stream_idle/hard_deadline) is
                    # strictly more specific than the legacy generic
                    # "provider_rate_or_5xx" bucket and is surfaced
                    # whenever available, even for a `partial` turn
                    # (e.g. `stream_idle_timeout` — real progress
                    # happened, then stalled — is diagnostically
                    # different from a clean zero-progress cutoff).
                    "failure_mode": _ic_failure_mode or (None if partial_invocations else "provider_rate_or_5xx"),
                    "recovery_action": (
                        "auto_backoff_then_retry" if _ic_failure_mode
                        else ("continue_or_edit_restart" if partial_invocations else "auto_backoff_then_retry")
                    ),
                }) + "\n\n"
            )
            state.timed_out = True
            return
        if ev["type"] == "tick":
            yield (
                "data: " + json.dumps({
                    "thinking":    True,
                    "elapsed_s":   ev["elapsed_s"],
                    "activity":    ev["activity"],
                    "invocations": ev.get("invocations") or [],
                    # 2026-09-08 — Turn-Completion Contract stream-idle
                    # notice — see stream.py's STREAM_SLOW_NOTICE_S.
                    "slow_notice": bool(ev.get("slow_notice", False)),
                }) + "\n\n"
            )
        elif ev["type"] == "mode":
            # Iter 42 — forward classified mode (A/B/C/D/E) to UI so
            # the pill renders BEFORE tokens stream.
            yield f"data: {json.dumps({'type': 'mode', 'mode': ev['mode']})}\n\n"
        elif ev["type"] == "step":
            # Iter 212m-18 — orchestrator phase event ("🤔 Thinking…",
            # "📖 Reading repo…", "✍️ Writing files…", "🚀 Committing…",
            # "✅ Done"). Streamed verbatim — frontend renders these
            # in the live progress strip.
            # Chat UX #4 (Tier 1) — also stash it so we can persist
            # the full sequence once the turn finishes.
            collected_steps.append({
                "text": ev.get("text", ""),
                "done": bool(ev.get("done", False)),
            })
            yield (
                "data: " + json.dumps({
                    "type": "step",
                    "text": ev.get("text", ""),
                    "done": bool(ev.get("done", False)),
                }) + "\n\n"
            )
        elif ev["type"] == "error":
            # Iter 339k — NEVER leak a raw Python exception string
            # into the chat bubble. Convert the error frame into an
            # empty result and fall through to the graceful
            # empty-content fallback below ("I wasn't able to
            # produce a reply…"), which includes a trimmed reason.
            logger.warning("chat_stream worker error frame: %s",
                           str(ev.get("error"))[:300])
            result = {"content": "", "error": str(ev.get("error") or "pipeline error")}
            break
        elif ev["type"] == "intent":
            # Iter 212m-149 — Intent Gateway frame.  UI uses this
            # to render the tier dot (casual/query/agentic) +
            # an optional clarifying probe when ambiguous.
            yield (
                "data: " + json.dumps({
                    "type":   "intent",
                    "intent": ev.get("intent") or {},
                }) + "\n\n"
            )
        elif ev["type"] == "result":
            result = ev["result"]
            break

    state.result = result
    state.collected_steps = collected_steps
    state.timed_out = False
