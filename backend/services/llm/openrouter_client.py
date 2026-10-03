"""services/llm/openrouter_client.py — OpenRouter transport layer.

Session D · D-2a (LLM Split Phase 4, 2026-02) — extracted from
`services/llm/__init__.py`. Owns:

  • The `OPENROUTER_URL` endpoint constant.
  • Retry + fallback policy constants (`_RETRY_STATUS`, `_MAX_RETRIES`,
    `_BASE_DELAY_S`, `_FALLBACK_STATUSES`, `_DEFAULT_FREE_MODELS`).
  • Pure classifier / helper functions (`_free_fallback_models`,
    `_is_fallback_worthy`, `_retryable`, `_retry_delay`).
  • The unified `call_openrouter_model` entry-point used by
    `services.agents._call` and internal `_call_claude`/`_call_glm`/
    `_call_longcat` transports.

The provider-specific helpers (`_call_deepseek_direct`, `_call_groq`)
remain in `services/llm/__init__.py` for D-2a — they'll move in
D-2b / D-2d respectively. To avoid a circular import,
`call_openrouter_model` uses LAZY imports for those two — the imports
resolve at call time when both modules are fully loaded.
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ─── Endpoint + retry policy ─────────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# 2026-09-12 — OpenRouter dashboard attribution. Fixed branding value,
# intentionally NOT read from the APP_URL env var (that one correctly
# tracks this deployment's OAuth-callback domain, currently aurem.dev
# — an unrelated concern).
_OPENROUTER_REFERER = "https://auremcto.com"

# Iter 124 — retry policy for transient upstream failures (rate limit / 5xx).
# Exponential backoff with full jitter so concurrent callers don't sync up.
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MAX_RETRIES = 1      # Iter 129 — was 3. 4 attempts × 5.6s of backoff
                      # was killing chat latency under load (every 429
                      # cascade burned ~10 s of wall time for no
                      # behavioural win — LLM rate limits are STICKY,
                      # repeated retries hit the same wall). 1 retry
                      # catches the flapping-network case and surfaces
                      # genuine outages to the user fast.
_BASE_DELAY_S = 0.4   # Iter 129 — was 0.8. Halves the worst-case
                      # delay window. With _MAX_RETRIES=1 this is the
                      # ONLY backoff that fires.


# ─── Iter 212m-47 — Free-model fallback chain ─────────────────────────────
# When OpenRouter rejects a paid-model call with one of these statuses
# (most commonly 402 = insufficient credits, also 429 / 5xx / network),
# we retry the SAME prompt against OpenRouter's `:free` models. These
# don't consume credits and live on independent infra so a credit-
# exhaustion or paid-model outage still gets the user a response.
#
# Order matters: best quality → smallest. Env override:
#   OPENROUTER_FREE_MODELS=model1,model2,model3
_FALLBACK_STATUSES = {402, 404, 408, 425, 429, 500, 502, 503, 504}
# 404 → primary model slug isn't routable on OpenRouter (rare config
# drift). Treat as "this candidate is broken — try the next" rather
# than a hard fail; the legitimate "your prompt is broken" 4xx codes
# (400, 422) still abort the chain.
_DEFAULT_FREE_MODELS = [
    # Verified-live slugs via GET /api/v1/models (Feb 2026). Order:
    # best quality → smallest. The chain stops at the first model that
    # produces non-empty content.
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-coder:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]


# ─── Pure helpers ────────────────────────────────────────────────────────

def _free_fallback_models() -> list[str]:
    raw = os.getenv("OPENROUTER_FREE_MODELS", "").strip()
    if not raw:
        return list(_DEFAULT_FREE_MODELS)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _is_fallback_worthy(exc: Exception) -> bool:
    """Return True when the exception means the PRIMARY model is the
    problem (credit issue, rate limit, upstream 5xx, network blip) and
    we should retry the same prompt against a free model. False when
    the prompt itself is broken (4xx other than 402/429) — retrying
    would just burn another model's quota for no win."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _FALLBACK_STATUSES
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                        httpx.ReadError, httpx.RemoteProtocolError)):
        return True
    return False


def _retryable(exc: Exception) -> tuple[bool, int | None]:
    """Return (should_retry, http_status). Status is None for non-HTTP errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        return (exc.response.status_code in _RETRY_STATUS,
                exc.response.status_code)
    # Network / timeout — transient by default
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                        httpx.ReadError, httpx.RemoteProtocolError)):
        return (True, None)
    return (False, None)


def _retry_delay(attempt: int) -> float:
    """Full-jitter exponential backoff. attempt is 1-indexed."""
    cap = _BASE_DELAY_S * (2 ** (attempt - 1))
    return random.uniform(0, cap)


# ─── Unified entry-point ─────────────────────────────────────────────────

async def call_openrouter_model(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1000,
    temperature: float = 0.2,
    feature: Optional[str] = None,
    reasoning: Optional[dict] = None,
    provider_prefs: Optional[dict] = None,
) -> str:
    """Iter 165 — generic OpenRouter caller for the agents pipeline.

    Used by services.agents._call for ANY OpenRouter model (Kimi K2,
    Kimi K2.7 Code, Kimi K2.5, Kimi Thinking, Claude via OpenRouter,
    DeepSeek). The legacy `_call_claude` path goes through the
    Emergent LLM key instead — this function is the unified
    OpenRouter-only path so smart_router can pick any provider.

    Iter 212m-47 — Free-model fallback chain. If the requested paid
    model returns 402 (insufficient credits) / 429 / 5xx / network
    error, we transparently retry the same prompt against OpenRouter's
    `:free` tier models. The caller never has to know — they still get
    a usable completion string, with a log line marking which model
    actually answered.

    2026-09-12 — `feature` (e.g. "Loop Planning", "Self-Heal Retry",
    "Vision Describe") tags the OpenRouter dashboard's `X-Title` per
    call-site so background/guard traffic is distinguishable from
    user-facing traffic in openrouter.ai/logs — previously every
    caller shared one generic "AUREM" title. Optional, defaults to
    the generic title for any caller that hasn't been tagged yet.

    2026-02 — `reasoning` (OpenRouter's unified reasoning-tokens
    param, e.g. `{"effort": "high"}`) and `provider_prefs` (OpenRouter's
    `provider` routing-preferences block, e.g. `{"data_collection":
    "deny"}`) are passed straight through into the request payload
    when given. Both default to `None` (omitted from the payload) so
    every existing caller keeps today's exact request shape — only
    call-sites that explicitly opt in (e.g. `_call_glm_review` for
    Maxx review depth + private-repo data handling) are affected.

    Returns "" on any failure; the agents layer handles fallback so we
    keep this function thin and predictable.
    """
    # Session D · D-2a — lazy imports for the provider-specific
    # helpers still living in `services/llm/__init__.py`. Module-level
    # imports here would create a circular dependency (the parent
    # `__init__.py` imports THIS module at load time). Late-binding
    # via function-scope imports is Python's canonical fix.
    from services.llm import (
        _openrouter_key, _deepseek_direct_key, _groq_key,
        _DEEPSEEK_DIRECT_MODEL, _GROQ_MODEL,
        _call_deepseek_direct, _call_groq,
    )
    from ._state import _set_last_provider, get_prompt_cache_context

    api_key = _openrouter_key()
    if not api_key:
        logger.warning("OPENROUTER_API_KEY missing — call_openrouter_model returning empty")
        return ""
    headers = {
        "Authorization": f"Bearer {api_key}",
        # 2026-09-12 — decoupled from the general-purpose APP_URL env
        # var (which correctly tracks this deployment's OAuth-callback
        # domain, currently aurem.dev) — OpenRouter attribution is a
        # fixed branding value, not a per-deployment redirect URL.
        "HTTP-Referer": _OPENROUTER_REFERER,
        "X-Title": f"AUREM - {feature}" if feature else "AUREM",
        "Content-Type": "application/json",
    }
    # 2026-09-22 — Section 16 cost-efficiency fix. Opt-in, flag-gated
    # prompt caching. `LLM_PROMPT_CACHING_ENABLED` defaults OFF; even
    # when on, this only ever fires for a caller that explicitly wrapped
    # itself in `prompt_cache_context(...)` (see _state.py) — every
    # other caller gets the EXACT same plain-string `system` content
    # and payload shape as before this change.
    #
    # `cache_control` (Anthropic's own mechanism, passed through by
    # OpenRouter) is the only one of the 4 providers in this file that
    # NEEDS an explicit marker — DeepSeek/GLM/LongCat cache
    # automatically server-side (confirmed via provider docs, see
    # SCALING_PLAN addendum / audit notes), so the marker is
    # DELIBERATELY restricted to `anthropic/` model slugs; applying it
    # to a non-Anthropic backend would be a silent no-op at best and
    # an unverified request-shape risk at worst.
    _caching_enabled = os.getenv("LLM_PROMPT_CACHING_ENABLED", "0") == "1"
    _cache_hint = get_prompt_cache_context() if _caching_enabled else {"session_id": None, "cache_system": False}
    _use_cache_control = bool(_cache_hint.get("cache_system")) and model.startswith("anthropic/")
    _sticky_session_id = _cache_hint.get("session_id")
    system_content = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if _use_cache_control else system
    )
    base_messages = [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user},
    ]
    _timeout_s = float(os.getenv("LLM_HTTP_TIMEOUT_S", "25.0"))
    # 2026-09-08 — QA regression report Result 3 (live-reproduced):
    # exact-phrase degeneration loop ("your page" repeated 20+ times
    # in a table cell) on a short/low-entropy generation. Confirmed
    # via code read that NO OpenRouter call anywhere in this codebase
    # passed frequency_penalty/presence_penalty — a well-known lever
    # for exactly this failure mode. A modest, OpenAI-schema-standard
    # value meaningfully suppresses exact-token-sequence repetition
    # without perceptibly changing normal answer quality. Env-
    # overridable for easy tuning without a redeploy.
    _freq_penalty = float(os.getenv("LLM_FREQUENCY_PENALTY", "0.4"))
    # 2026-09-11 — founder re-repro'd the SAME exact-phrase degeneration
    # ("your page" x20+ in one table cell) live in production AFTER the
    # frequency_penalty fix above had already deployed (confirmed via
    # /api/health build_hash match). Root cause of the miss: OpenRouter
    # forwards `frequency_penalty` to the underlying engine (vLLM/SGLang
    # for GLM-5.2 + LongCat-2.0, both used here), but that alone doesn't
    # reliably stop a determined EXACT-token-sequence loop — that's what
    # `repetition_penalty` (a distinct, multiplicative lever, default 1.0
    # = neutral) is specifically for. Using a light 1.1 value (GLM-5.2's
    # own docs recommend ~1.05 to avoid heavier values causing cross-
    # lingual "drift") to stack with frequency_penalty for this failure
    # class without perceptibly changing normal answers.
    _rep_penalty = float(os.getenv("LLM_REPETITION_PENALTY", "1.1"))

    # Try the requested model first, then each free fallback in turn.
    candidates = [model] + [m for m in _free_fallback_models() if m != model]
    last_exc: Exception | None = None
    for i, candidate in enumerate(candidates):
        payload = {
            "model": candidate,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "frequency_penalty": _freq_penalty,
            "repetition_penalty": _rep_penalty,
            "messages": base_messages,
        }
        if _sticky_session_id:
            # 2026-09-22 — Section 16 fix. Sticky routing hint so
            # repeat calls in the same session land on the same
            # OpenRouter backend replica — this is what lets
            # DeepSeek/GLM/LongCat's AUTOMATIC server-side caching
            # actually get a hit instead of round-robining onto a
            # cold replica each time. Harmless if the model/provider
            # ignores it.
            payload["session_id"] = _sticky_session_id
        if reasoning:
            # 2026-02 — unified OpenRouter reasoning-tokens param
            # (e.g. {"effort": "high"}). Passed through verbatim only
            # when a caller opts in — see _call_glm_review.
            payload["reasoning"] = reasoning
        if provider_prefs:
            # 2026-02 — OpenRouter provider-routing preferences (e.g.
            # {"data_collection": "deny"} to restrict which upstream
            # hosts may serve the request). Passed through verbatim
            # only when a caller opts in — see _call_glm / _call_glm_review.
            payload["provider"] = provider_prefs
        # 2026-09-27 — OpenRouter's own documented end-user attribution
        # field (see openrouter.ai/docs/cookbook/administration/user-
        # tracking). Only set when a real caller tagged the ambient
        # context via `llm_user_context(...)` — internal guard/health-
        # check calls with no end-user attached simply omit it.
        from services.loop_token_ledger import current_ambient_user_id
        _ambient_user = current_ambient_user_id()
        if _ambient_user:
            payload["user"] = _ambient_user
        _t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_timeout_s) as c:
                r = await c.post(OPENROUTER_URL, headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
            _latency_ms = (time.monotonic() - _t0) * 1000
            _msg = data["choices"][0]["message"]
            # 2026-08-25 — defensive guard (see openrouter_providers.py
            # `_call_deepseek` for the customer-facing incident this
            # fixed): a non-dict `message` shape must raise a normal
            # Exception the surrounding try/except already handles,
            # not an uncaught AttributeError.
            if not isinstance(_msg, dict):
                raise TypeError(f"message is {type(_msg).__name__}, expected dict")
            content = (_msg.get("content") or "").strip()
            if _caching_enabled:
                # 2026-09-22 — measurement hook. Anthropic/OpenRouter
                # surface cache hit/write token counts inside `usage`
                # (`cache_read_input_tokens` / `cache_creation_input_tokens`,
                # or the OpenAI-normalized `prompt_tokens_details.cached_tokens`
                # depending on provider). Log-only — never changes the
                # return shape — so a live before/after run can be read
                # straight out of the backend log.
                logger.info(
                    "prompt_caching: model=%s cache_system=%s session_id=%s usage=%r",
                    candidate, _use_cache_control, bool(_sticky_session_id),
                    (data or {}).get("usage"),
                )
            # Iter 309 · Pre-Phase-1 — loop-token accounting.
            # No-op unless a loop context is active (see
            # loop_token_ledger.loop_call_context).
            try:
                from services.loop_token_ledger import log_llm_usage
                await log_llm_usage(
                    candidate,
                    (data or {}).get("usage") or {},
                    temperature=temperature,
                )
            except Exception as _e:
                # Same rationale as _call_deepseek:738 — non-fatal
                # fail-open, but log at debug for grep-ability.
                logger.debug(
                    "[silent-catch] llm.py:1104 in call_openrouter_model "
                    "— loop_token_ledger.log_llm_usage failed: %r", _e,
                )
            # 2026-09-27 P0 — REAL per-user cost ledger. Unconditional
            # (unlike log_llm_usage above, which is loop-only): every
            # real OpenRouter call, chat/scan/loop/everything, now
            # writes its REAL provider-reported cost — this is the
            # root-cause fix for the token-meter showing "Used: 0"
            # despite real chat/scan activity.
            try:
                from services.loop_token_ledger import log_real_usage
                await log_real_usage(
                    candidate,
                    (data or {}).get("usage") or {},
                    generation_id=(data or {}).get("id"),
                    feature=feature,
                    latency_ms=_latency_ms,
                    status="ok",
                )
            except Exception as _e:
                logger.debug(
                    "[silent-catch] call_openrouter_model — "
                    "loop_token_ledger.log_real_usage failed: %r", _e,
                )
            if i > 0:
                logger.warning(
                    "call_openrouter_model: primary %r failed, served by free fallback %r",
                    model, candidate,
                )
                _set_last_provider("openrouter", candidate)
            else:
                _set_last_provider("openrouter", candidate)
            return content
        except Exception as e:
            last_exc = e
            if not _is_fallback_worthy(e):
                # Prompt-level bug (4xx other than 402/429). Don't burn
                # extra free quota — surface the failure.
                logger.warning("call_openrouter_model(%s) non-retryable: %r", candidate, e)
                try:
                    from services.loop_token_ledger import log_real_usage
                    await log_real_usage(
                        candidate, {}, feature=feature,
                        latency_ms=(time.monotonic() - _t0) * 1000,
                        status="failed", error_reason=f"{type(e).__name__}: {str(e)[:200]}",
                    )
                except Exception as _e:
                    logger.debug("[silent-catch] call_openrouter_model failed-row log skipped: %r", _e)
                return ""
            logger.warning(
                "call_openrouter_model(%s) fallback-worthy failure (%d/%d): %r",
                candidate, i + 1, len(candidates), e,
            )
            # Iter 212m-51 — after the PRIMARY OpenRouter model fails
            # (and before walking the OpenRouter :free chain), try
            # DeepSeek direct. Same priority order as `_call_deepseek`
            # so all paths (chat / agents / Vanguard / Mode D) share
            # the same vendor-independent failover.
            if i == 0 and _deepseek_direct_key():
                try:
                    logger.warning(
                        "call_openrouter_model: primary %r failed, "
                        "trying DeepSeek direct (model=%s)…",
                        model, _DEEPSEEK_DIRECT_MODEL,
                    )
                    ds_content = await _call_deepseek_direct(
                        messages=[{"role": "user", "content": user}],
                        system=system,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    if ds_content:
                        logger.warning(
                            "call_openrouter_model: served by DeepSeek-direct model=%s",
                            _DEEPSEEK_DIRECT_MODEL,
                        )
                        _set_last_provider("deepseek_direct", _DEEPSEEK_DIRECT_MODEL)
                        return ds_content
                except Exception as dse:
                    if isinstance(dse, httpx.HTTPStatusError) and \
                       dse.response.status_code in (400, 422):
                        logger.warning(
                            "DeepSeek direct rejected agent prompt (%d) — aborting chain",
                            dse.response.status_code,
                        )
                        return ""
                    # 401/402/429/5xx → bad key / balance / vendor
                    # issue. Walk forward instead of failing the
                    # whole agent call.
                    logger.warning(
                        "DeepSeek direct failed inside call_openrouter_model (%r) — "
                        "walking OR free chain", dse,
                    )
    # Iter 212m-49 — All OpenRouter candidates exhausted. Try the Groq
    # emergency net before giving up. Vendor-independent infra so an
    # OpenRouter-wide outage / global-free-tier 429 storm still gets
    # the user a usable answer.
    if _groq_key():
        try:
            logger.warning(
                "call_openrouter_model: chain exhausted, trying Groq emergency "
                "(model=%s) for primary=%r",
                _GROQ_MODEL, model,
            )
            content = await _call_groq(
                messages=[{"role": "user", "content": user}],
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if content:
                logger.warning(
                    "call_openrouter_model: served by Groq fallback model=%s",
                    _GROQ_MODEL,
                )
                _set_last_provider("groq", _GROQ_MODEL)
                return content
        except Exception as ge:
            logger.error(
                "Groq emergency fallback ALSO failed inside call_openrouter_model: %r",
                ge,
            )
    logger.error(
        "call_openrouter_model: all %d candidates exhausted (primary=%r). Last error: %r",
        len(candidates), model, last_exc,
    )
    try:
        from services.loop_token_ledger import log_real_usage
        await log_real_usage(
            model, {}, feature=feature, status="failed",
            error_reason=f"chain_exhausted: {type(last_exc).__name__ if last_exc else 'unknown'}: "
                         f"{str(last_exc)[:200] if last_exc else 'no candidates'}",
        )
    except Exception as _e:
        logger.debug("[silent-catch] call_openrouter_model exhausted-row log skipped: %r", _e)
    return ""
