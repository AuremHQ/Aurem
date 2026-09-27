"""
services/loop_token_ledger.py — Iter 309 · Pre-Phase-1

Per-loop LLM token accounting.  Uses `contextvars` so a loop's
LLM calls carry (loop_id, phase_tag) transparently through every
nested function call — including the Parliament / vanguard-verify
paths that don't know they're being invoked inside a loop.

Design goals
------------
• Zero API surface change for the LLM callers themselves.  The
  low-level `_call_deepseek` / `_call_claude` / `_call_glm`
  functions in `services/llm.py` only need to call
  `log_llm_usage(model, usage_dict)` after a successful response;
  this ledger reads the ambient context and forwards to the
  already-shipped `cost_tracker.log_call()` when — and ONLY when
  — a loop context is active.  Regular chat / scaffold / deep-
  research callers are unaffected.

• Uses the existing `ora_chat_usage` collection so `/admin/
  loop-token-metrics` doesn't need a new schema or index.  Every
  loop-originated row carries `session_id = loop_id`,
  `route = "loop.<phase_tag>"`, and `user_id` from the ambient
  engine.  `/admin/loop-metrics` continues to work; loop-token
  metrics filter `route ^= "loop."`.

• Founder review 2026-07-26 — Pricing lookup is already shipped
  in `cost_tracker.compute_cost_usd()`.  We reuse it (no new
  price tables) so cost_usd lands automatically.
"""
from __future__ import annotations

import contextvars
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)


# ── Ambient context ────────────────────────────────────────────────
# ContextVars are async-safe: each asyncio task gets its own copy,
# so a Parliament fan-out (3 concurrent LLM calls) doesn't leak
# one file's phase tag into another file's ledger row.
_LOOP_ID_CV: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "loop_token_ledger.loop_id", default=None,
)
_PHASE_TAG_CV: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "loop_token_ledger.phase_tag", default=None,
)
_USER_ID_CV: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "loop_token_ledger.user_id", default=None,
)
# 2026-08 hardening (F3) — per-call agent label, layered ON TOP of the
# phase-level loop context above. Council premium pricing needs each of
# the 3 council-member votes + the CEO call + a single-model fallback
# call separable, not one "loop.execute" blob. Nested/scoped per call
# (not per phase) — see agent_call_context().
_AGENT_LABEL_CV: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "loop_token_ledger.agent_label", default=None,
)

# 2026-09-27 P0 — usage-tracking root-cause fix. Ambient user tag for
# ANY LLM call path (chat, scan, loop, agents) — NOT gated to loop
# mode like `_USER_ID_CV` above. Set once at the single shared funnel
# (`call_llm_with_meta`) and read at the bottom (`call_openrouter_model`)
# so every real OpenRouter call can be tagged with the real caller's
# user_id without threading a new parameter through every intermediate
# agent/function signature in between (ReaderAgent, CoderAgent, _call,
# _call_claude/_call_glm/_call_longcat — none of which know about
# user_id today). Same contextvars pattern already proven by the loop-
# only context above, just not gated to an active loop.
_AMBIENT_USER_ID_CV: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "loop_token_ledger.ambient_user_id", default=None,
)


@asynccontextmanager
async def llm_user_context(user_id: Optional[str]):
    """General-purpose ambient user tag for ANY LLM call (chat, scan,
    loop). Read by `call_openrouter_model` to (a) tag the OpenRouter
    request's `user` field for real per-end-user attribution
    (OpenRouter's own documented mechanism), and (b) tag the real-cost
    ledger row (`log_real_usage`, below). Never raises."""
    t = _AMBIENT_USER_ID_CV.set(user_id or None)
    try:
        yield
    finally:
        _AMBIENT_USER_ID_CV.reset(t)


def current_ambient_user_id() -> Optional[str]:
    """Prefer the loop-scoped user_id when a loop is active (more
    specific / already validated), else fall back to the general
    ambient tag set by `llm_user_context`."""
    _, _, loop_user = current_loop_context()
    return loop_user or _AMBIENT_USER_ID_CV.get()


@asynccontextmanager
async def agent_call_context(agent_label: str):
    """Wrap a SINGLE LLM call (not a whole phase) so its ledger row is
    tagged with which agent made it — e.g. "council-a1", "council-a2",
    "council-a3", "ceo", "single-model". Nests inside an outer
    `loop_call_context(...)`; never raises."""
    t = _AGENT_LABEL_CV.set(agent_label)
    try:
        yield
    finally:
        _AGENT_LABEL_CV.reset(t)


@asynccontextmanager
async def loop_call_context(*, loop_id: str, phase_tag: str,
                             user_id: Optional[str] = None):
    """Wrap the code that will trigger LLM calls so every downstream
    `_call_deepseek` / `_call_claude` / `_call_glm` writes a row
    into `ora_chat_usage` tagged with this loop_id + phase_tag.

    Usage in `loop_engine.py::_with_budget`:

        async with loop_call_context(
            loop_id=self.loop_id, phase_tag=phase,
            user_id=self.user_id,
        ):
            await asyncio.wait_for(coro(), timeout=budget)

    All LLM calls fired from `coro()` — even three levels deep in
    Parliament — will carry the same tag.  Nested contexts stack
    (inner replaces outer for its scope, then restores).  Never
    raises; ledger errors are logged and swallowed so an LLM call
    is never blocked by an accounting hiccup.
    """
    t_lid = _LOOP_ID_CV.set(loop_id)
    t_ph  = _PHASE_TAG_CV.set(phase_tag)
    t_uid = _USER_ID_CV.set(user_id or "")
    try:
        yield
    finally:
        _LOOP_ID_CV.reset(t_lid)
        _PHASE_TAG_CV.reset(t_ph)
        _USER_ID_CV.reset(t_uid)


def current_loop_context() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (loop_id, phase_tag, user_id) or (None, None, None)."""
    return _LOOP_ID_CV.get(), _PHASE_TAG_CV.get(), _USER_ID_CV.get()


# ── Fire-and-forget ledger write ───────────────────────────────────
async def log_llm_usage(model: str, usage: dict,
                         *, temperature: float = 0.0,
                         error: Optional[str] = None) -> None:
    """Called by the low-level LLM callers immediately after a
    successful OpenRouter response is parsed.  If no loop context
    is active (regular chat / scaffold call), returns immediately —
    the call becomes a no-op for non-loop callers.

    `usage` is the raw dict from the response, e.g.:
      {"prompt_tokens": 812, "completion_tokens": 240, "total_tokens": 1052}

    We accept a couple of naming conventions so the caller doesn't
    have to normalize:
      • OpenRouter-native: prompt_tokens / completion_tokens
      • ora_chat-style:    input_tokens  / output_tokens
    """
    loop_id, phase_tag, user_id = current_loop_context()
    if not loop_id or not phase_tag:
        return  # No loop context → no-op.  Regular chat unchanged.

    input_tokens  = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    output_tokens = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )

    # Skip zero-token rows so a fallback-chain path that hit an
    # error before receiving usage doesn't spam the ledger.
    if input_tokens == 0 and output_tokens == 0 and not error:
        return

    try:
        # Route encodes loop-phase (+ agent, when set) so
        # `/admin/loop-token-metrics` can filter with `route ^= "loop."`
        # and group by phase — and 2026-08 hardening (F3) group by
        # agent for future Council-premium per-agent pricing.
        agent_label = _AGENT_LABEL_CV.get()
        route = f"loop.{phase_tag}.{agent_label}" if agent_label else f"loop.{phase_tag}"
        # Reuse the shipped cost_tracker so cost_usd is computed
        # from the existing price table (founder-approved reuse
        # per iter 309 pre-Phase-1 scope).
        from services.ora_chat import cost_tracker as _ct
        await _ct.log_call(
            user_id=user_id or "loop-orphan",
            session_id=loop_id,
            route=route,
            model=model,
            temperature=temperature,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error=error,
        )
    except Exception as e:      # noqa: BLE001
        # Ledger MUST never break an LLM call.  Warn once and move on.
        logger.warning(
            "loop_token_ledger.log_llm_usage failed "
            "(loop=%s phase=%s model=%s): %r",
            loop_id, phase_tag, model, e,
        )


# ── 2026-09-27 — REAL (provider-reported) per-user cost ledger ────────
# Root-cause fix for the "Used: 0" token-meter bug (P0, live evidence
# on mooreasys@gmail.com). Everything above this line (`log_llm_usage`)
# is a NO-OP outside of Loop mode, and even inside Loop mode it logs an
# ESTIMATE (cost_tracker's static $/M-token price table), never the
# real OpenRouter-billed number. This function is different on both
# counts: (1) unconditional — fires for every real OpenRouter call,
# chat/scan/loop/everything, tagged via `current_ambient_user_id()`
# rather than requiring an active loop context; (2) uses the REAL
# `usage.cost` OpenRouter returns inline on every chat-completion
# response (confirmed via OpenRouter's own docs — no separate
# /generation lookup needed), not a heuristic estimate.
async def log_real_usage(model: str, usage: dict, *,
                          generation_id: Optional[str] = None,
                          feature: Optional[str] = None) -> None:
    """Called by `call_openrouter_model` immediately after every
    successful OpenRouter response. Writes one row to
    `real_llm_usage` tagged with the ambient user_id (or "anonymous"
    if none is set — e.g. an internal guard/health-check call with no
    end-user attached). Never raises; a ledger failure must never
    break the LLM call it's describing."""
    prompt_tokens     = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    if prompt_tokens == 0 and completion_tokens == 0:
        return  # No usage reported (e.g. a fallback path) — nothing real to log.
    real_cost_usd = float(usage.get("cost") or 0.0)
    user_id = current_ambient_user_id() or "anonymous"
    try:
        from cto_services.db import get_db
        db = get_db()
        await db.real_llm_usage.insert_one({
            "user_id":            user_id,
            "model":              model,
            "prompt_tokens":      prompt_tokens,
            "completion_tokens":  completion_tokens,
            "total_tokens":       prompt_tokens + completion_tokens,
            "cost_usd":           real_cost_usd,
            "generation_id":      generation_id,
            "feature":            feature,
            "loop_id":            _LOOP_ID_CV.get(),
            "ts":                 time.time(),
        })
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "loop_token_ledger.log_real_usage failed (user=%s model=%s): %r",
            user_id, model, e,
        )
