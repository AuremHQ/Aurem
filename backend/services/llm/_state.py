"""
services/_llm_state.py — LLM.py 3-Way Split · Phase 0a

Shared-state submodule for `services/llm.py`. Owns the mutable
containers and helpers that MUST have a single canonical instance
across the split (contextvars, provenance stash, LongCat probe
snapshot).

⚠️  Phase 0a scope only ⚠️
This file holds the SAFE-to-move state:
  - `_new_provenance_slot()` factory
  - `_last_provider_ctx` ContextVar
  - `_set_last_provider`, `get_last_provider`, `reset_last_provider`
  - `_LONGCAT_LAST_PROBE` dict

The `LONGCAT_LIVE` bool is DELIBERATELY LEFT IN `llm.py` for now
(Phase 0b). External callers do `services.llm.LONGCAT_LIVE = X`
writes; moving the bool cleanly requires a `types.ModuleType.__setattr__`
hook on `services.llm` to route those writes here. That's a separate
mechanical step, tracked in `memory/LLM_SPLIT_MIGRATION_PLAN.md`.

Everything in this module is imported UNCHANGED by `services/llm.py`
so external attribute access (`llm._last_provider_ctx`,
`llm.get_last_provider`, etc.) keeps working via a re-export line.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Optional


# ═══ Provenance stash ═══════════════════════════════════════════
# The non-streaming `_call_deepseek` returns a plain string so the
# existing SSE pipeline can't attach "served by Groq" metadata to
# the response object directly. We park the most-recent provider
# in a MUTABLE dict referenced from a ContextVar so concurrent
# requests never read each other's provenance, AND child asyncio
# tasks (the chat_stream worker) can WRITE to the same dict the
# parent task reads from. Plain ContextVar.set() in a child task
# is invisible to the parent because each task gets its own context
# copy — so we mutate the dict in place instead.
def _new_provenance_slot() -> dict:
    return {"provider": "openrouter", "model": "", "is_emergency": False}


_last_provider_ctx: ContextVar[dict] = ContextVar(
    "aurem_last_llm_provider",
    default=_new_provenance_slot(),
)


def _set_last_provider(provider: str, model: str) -> None:
    slot = _last_provider_ctx.get()
    slot["provider"]     = provider
    slot["model"]        = model
    slot["is_emergency"] = (provider == "groq")


def get_last_provider() -> dict:
    """Returns provenance for the last LLM call on THIS request context.
    Shape: {"provider": "openrouter"|"groq", "model": "<slug>",
    "is_emergency": True iff served by the Groq emergency fallback}.
    Reset per-request via the contextvar so concurrent users never
    cross-pollute."""
    return dict(_last_provider_ctx.get())


def reset_last_provider() -> None:
    """Call at the START of a request to clear stale provenance from a
    previous turn in the same worker. Installs a FRESH mutable dict
    so child tasks spawned later inherit a clean slot AND mutations
    they perform are visible to the parent (the dict reference is
    shared, only the ContextVar copy semantics break parent-child
    propagation of `ContextVar.set` calls)."""
    _last_provider_ctx.set(_new_provenance_slot())


# ═══ LongCat probe snapshot ═════════════════════════════════════
# Iter 212m-192 — In-memory snapshot of the latest probe outcome so
# the admin API can render a live badge without re-probing on every
# request. Written by `probe_longcat_availability()` and read by
# `routers/admin.py:council_health`. Never a source of truth over
# `LONGCAT_LIVE` — this dict just adds context (last error, epoch).
#
# NOTE: `_LONGCAT_LAST_PROBE["live"]` MUST stay in sync with
# `services.llm.LONGCAT_LIVE` (still in llm.py during Phase 0a).
# The producers (`probe_longcat_availability`, `_call_longcat`)
# already keep both up to date.
_LONGCAT_LAST_PROBE: dict = {
    "live":       True,           # mirror of services.llm.LONGCAT_LIVE
    "checked_at": 0.0,            # epoch seconds
    "http_code":  None,           # None when never probed / network error
    "error":      None,           # short string; None on success
    "model":      "",             # resolved model slug at probe time
    "enabled":    False,          # LONGCAT_ENABLED at probe time
}


# ═══ Prompt-caching hint (Section 16 cost-efficiency fix, 2026-09-22) ═══
# Advisory-only ambient context read by `call_openrouter_model`
# (Claude/GLM/LongCat) and `_call_deepseek` — NOT a new kwarg threaded
# through every one of `_meta.py`'s ~10 call sites (that would be a
# much larger, riskier diff for the same result). A caller that knows
# its `system` string is genuinely static/reused (Parliament Council
# personas, an orchestrator follow-up tool-loop iteration) wraps the
# call in `prompt_cache_context(...)`; every other caller sees the
# inert default below and gets ZERO behavior change — the transport
# layer additionally requires `LLM_PROMPT_CACHING_ENABLED=1` before it
# acts on this at all (see openrouter_client.py / openrouter_providers.py).
#
# `cache_system=True` is a HINT, not a guarantee of correctness — the
# transport layer still gates the actual `cache_control` marker to
# Claude-family models only (the one provider in this stack that
# needs an explicit marker; DeepSeek/GLM/Groq cache automatically
# server-side per provider docs, no marker possible or needed).
_prompt_cache_ctx: ContextVar[dict] = ContextVar(
    "aurem_prompt_cache_context",
    default={"session_id": None, "cache_system": False},
)


def get_prompt_cache_context() -> dict:
    return dict(_prompt_cache_ctx.get())


@asynccontextmanager
async def prompt_cache_context(*, session_id: Optional[str] = None,
                                cache_system: bool = False):
    """Scope a block of code (one or more LLM calls) with a caching
    hint. ContextVar-scoped per asyncio task, same isolation guarantee
    as `_last_provider_ctx` above — concurrent requests never see each
    other's hint. Always resets on exit, even on exception."""
    token = _prompt_cache_ctx.set({"session_id": session_id, "cache_system": cache_system})
    try:
        yield
    finally:
        _prompt_cache_ctx.reset(token)
