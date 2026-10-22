"""
services/ora_chat/providers.py — Iter 212m-238

Thin streaming caller over OpenRouter. Reuses the existing
`OPENROUTER_API_KEY` from `services/llm.py`; introduces NO new SDK
and NO new key.

Public API:
    async for chunk in stream_call(model, messages, temperature, top_p,
                                    presence_penalty, max_tokens): ...
    → yields dict events {type, ...}:
        {"type": "delta",   "content": "..."}     — token chunk
        {"type": "usage",   "input_tokens": N,
                            "output_tokens": M}   — final usage totals
        {"type": "done"}                          — stream complete
        {"type": "error",   "error": "reason"}    — mid-stream failure

    async def one_shot(...)  — non-streaming variant (used by the
    slash-explain formatter which is short + doesn't need SSE).
"""
from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT_S = float(os.getenv("ORA_LLM_TIMEOUT_S", "45.0"))


def _key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def _headers(feature: Optional[str] = None) -> dict:
    return {
        "Authorization": f"Bearer {_key()}",
        "HTTP-Referer": "https://auremcto.com",
        "X-Title": f"AUREM - {feature}" if feature else "AUREM - ORA Chat",
        "Content-Type": "application/json",
    }


def _payload(*, model: str, messages: list, temperature: float,
             top_p: float, presence_penalty: float, max_tokens: int,
             stream: bool) -> dict:
    p: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "presence_penalty": presence_penalty,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if stream:
        # Request usage stats in the final chunk when streaming.
        p["usage"] = {"include": True}
    return p


async def stream_call(*,
                      model: str,
                      messages: list,
                      temperature: float,
                      top_p: float,
                      presence_penalty: float,
                      max_tokens: int,
                      feature: Optional[str] = None) -> AsyncIterator[dict]:
    """Yield event dicts from an OpenRouter streaming completion.

    Never raises — errors are yielded as `{"type": "error", ...}` so
    the SSE consumer can render a partial-plus-error state cleanly.
    """
    if not _key():
        yield {"type": "error", "error": "openrouter_key_missing"}
        return

    body = _payload(model=model, messages=messages, temperature=temperature,
                    top_p=top_p, presence_penalty=presence_penalty,
                    max_tokens=max_tokens, stream=True)

    got_any = False
    usage: dict = {}
    content_chars = 0
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
            async with c.stream("POST", OPENROUTER_URL,
                                headers=_headers(feature), json=body) as r:
                if r.status_code != 200:
                    txt = ""
                    try:
                        txt = (await r.aread()).decode("utf-8", "ignore")[:400]
                    except Exception:
                        pass
                    yield {"type": "error",
                           "error": f"http_{r.status_code}",
                           "detail": txt}
                    return
                async for raw in r.aiter_lines():
                    if not raw or not raw.startswith("data:"):
                        continue
                    data = raw[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # Choice delta
                    choices = obj.get("choices") or []
                    if choices:
                        delta = (choices[0] or {}).get("delta") or {}
                        content = delta.get("content")
                        if content:
                            got_any = True
                            content_chars += len(content)
                            yield {"type": "delta", "content": content}
                    # Usage — OpenRouter emits this in a trailing chunk
                    # when `usage.include=true` is set.
                    u = obj.get("usage")
                    if u:
                        usage = {
                            "input_tokens":  int(u.get("prompt_tokens", 0) or 0),
                            "output_tokens": int(u.get("completion_tokens", 0) or 0),
                            "total_tokens":  int(u.get("total_tokens", 0) or 0),
                            "cost":          u.get("cost"),
                        }
    except httpx.TimeoutException:
        await _log_usage(model, usage, feature, content_chars, messages,
                         status="failed", error_reason="timeout")
        yield {"type": "error", "error": "timeout",
               "recoverable": got_any}
        return
    except Exception as e:  # noqa: BLE001
        logger.warning("ora_chat stream_call unexpected: %r", e)
        await _log_usage(model, usage, feature, content_chars, messages,
                         status="failed", error_reason=f"{type(e).__name__}: {e}")
        yield {"type": "error", "error": f"{type(e).__name__}",
               "detail": str(e)[:200], "recoverable": got_any}
        return

    await _log_usage(model, usage, feature, content_chars, messages)
    yield {"type": "usage", **usage}
    yield {"type": "done"}


_CHARS_PER_TOKEN_ESTIMATE = 4  # rough, provider-agnostic fallback only


async def _log_usage(model: str, usage: dict, feature: Optional[str],
                     content_chars: int, messages: list, *,
                     status: str = "ok",
                     error_reason: Optional[str] = None) -> None:
    """Closing-the-bypass-doors audit (2026-10) — this module used to
    make its own raw OpenRouter calls with zero usage logging, the
    biggest single unmetered LLM path in the backend (reused by chat,
    deep research, the hallucination classifier, and both independent
    verifiers). Routes through the SAME canonical ledger every other
    LLM transport uses, exactly like services/llm/jev_client.py.

    When OpenRouter doesn't send a trailing usage chunk (rare, but
    `stream_call`'s own docstring already anticipated a mid-stream
    failure), falls back to a character-length estimate — ~4 chars per
    token is a crude, provider-agnostic rule of thumb, not a real
    tokenizer (no new dependency added for this) — and marks the row
    `estimated=True` so cost reports can tell real counts from guesses.
    Never raises; a ledger failure must never break the chat response
    it's describing."""
    try:
        from services.loop_token_ledger import log_real_usage
        estimated = False
        has_real_tokens = bool(usage) and (
            usage.get("input_tokens") or usage.get("output_tokens")
        )
        if has_real_tokens:
            # Pass "cost" through as-is — log_real_usage prefers the
            # provider's own real cost over its pricing-table fallback.
            clean_usage = dict(usage)
        elif status == "ok" and content_chars > 0:
            estimated = True
            prompt_chars = sum(len(str(m.get("content") or "")) for m in (messages or []))
            clean_usage = {
                "input_tokens": max(1, prompt_chars // _CHARS_PER_TOKEN_ESTIMATE),
                "output_tokens": max(1, content_chars // _CHARS_PER_TOKEN_ESTIMATE),
            }
        else:
            clean_usage = {}
        await log_real_usage(
            model, clean_usage, feature=feature, provider="openrouter",
            status=status, error_reason=error_reason, estimated=estimated,
        )
    except Exception as e:                                    # noqa: BLE001
        logger.debug("ora_chat providers: usage ledger write skipped: %r", e)


async def one_shot(*,
                   model: str,
                   messages: list,
                   temperature: float,
                   top_p: float,
                   presence_penalty: float,
                   max_tokens: int,
                   feature: Optional[str] = None) -> tuple[str, dict, Optional[str]]:
    """Non-streaming call. Returns (content, usage_dict, error_or_none).

    Used for short deterministic responses (slash-command explanations,
    tests). Same key + endpoint as streaming path.
    """
    if not _key():
        return "", {}, "openrouter_key_missing"

    body = _payload(model=model, messages=messages, temperature=temperature,
                    top_p=top_p, presence_penalty=presence_penalty,
                    max_tokens=max_tokens, stream=False)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
            r = await c.post(OPENROUTER_URL, headers=_headers(feature), json=body)
            if r.status_code != 200:
                await _log_usage(model, {}, feature, 0, messages,
                                 status="failed", error_reason=f"http_{r.status_code}")
                return "", {}, f"http_{r.status_code}: {r.text[:200]}"
            j = r.json()
            content = ((j.get("choices") or [{}])[0].get("message") or {}
                       ).get("content") or ""
            u = j.get("usage") or {}
            usage = {
                "input_tokens":  int(u.get("prompt_tokens", 0) or 0),
                "output_tokens": int(u.get("completion_tokens", 0) or 0),
                "total_tokens":  int(u.get("total_tokens", 0) or 0),
                "cost":          u.get("cost"),
            }
            await _log_usage(model, usage, feature, len(content), messages)
            return content, usage, None
    except Exception as e:  # noqa: BLE001
        await _log_usage(model, {}, feature, 0, messages,
                         status="failed", error_reason=f"{type(e).__name__}: {str(e)[:200]}")
        return "", {}, f"{type(e).__name__}: {str(e)[:200]}"
