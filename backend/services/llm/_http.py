"""services/llm/_http.py — shared HTTP clients (P1/L3).

Every LLM call site used to build a FRESH client per call:

  • ``httpx.AsyncClient(...)`` inside ``call_openrouter_model``,
    ``_call_deepseek`` and ``_call_deepseek_direct`` — a new TCP/TLS
    handshake + connection pool for every request, then thrown away;
  • ``AsyncOpenAI(...)`` inside every ``ora_chat_v2.llm_client.
    stream_chat`` call;
  • ``AsyncGroq(...)`` inside every ``_call_groq`` call.

This module owns the process-wide singletons. Clients are keyed by
the parameters that actually differ between call sites (timeout for
httpx; base_url + api_key + timeout + headers for OpenAI; api_key +
timeout for Groq) and created ONCE, then reused. Callers must NEVER
close a client they got from here (no ``async with``) — the pool
lives for the process lifetime.

Event-loop guard: a cached client belongs to the loop it first ran
on. Uvicorn workers run one loop for the process lifetime, so in
production the guard never fires. Test harnesses that recreate the
loop per test (``asyncio.run`` per case) would otherwise reuse a
client whose connections belong to a dead loop — on a loop change
the stale entry is dropped and a fresh client is created. Closing a
client bound to a dead loop is impossible from the new loop, so the
stale one is dropped, not closed (its sockets die with its loop).

``aclose_shared_clients()`` is for tests / graceful shutdown only.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# key -> (event loop the client first ran on, client)
_CLIENTS: dict = {}


def _running_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _get_or_create(key, factory):
    loop = _running_loop()
    entry = _CLIENTS.get(key)
    if entry is not None:
        old_loop, client = entry
        if loop is None or old_loop is None or old_loop is loop:
            return client
        # Loop changed (test harness recreating the loop): drop the
        # stale client — its connections belong to the dead loop.
        # Log only the client kind, never the key: keys can contain
        # raw API keys.
        logger.debug("llm/_http: dropping shared %s client for new loop",
                     key[0] if key else "?")
        _CLIENTS.pop(key, None)
    client = factory()
    _CLIENTS[key] = (loop, client)
    return client


def get_shared_client(timeout: float = 25.0) -> httpx.AsyncClient:
    """Process-wide ``httpx.AsyncClient`` for OpenRouter / DeepSeek-
    direct calls, keyed by timeout. Call sites use 25.0 (OpenRouter)
    and 30.0 (DeepSeek-direct); each timeout gets its own client so
    no call site's timeout semantics change.

    Do NOT close the returned client — it is shared.
    """
    timeout = float(timeout)
    return _get_or_create(
        ("httpx", timeout),
        lambda: httpx.AsyncClient(timeout=timeout),
    )


def get_shared_openai_client(*, base_url: str, api_key: str,
                             timeout: float = 45.0,
                             default_headers: Optional[dict] = None):
    """Process-wide ``AsyncOpenAI`` client, keyed by (base_url, api_key,
    timeout, headers). Used by ``ora_chat_v2.llm_client.stream_chat``,
    which used to construct a client per call. Same rule: never close.
    """
    from openai import AsyncOpenAI

    timeout = float(timeout)
    headers = dict(default_headers or {})
    key = ("openai", base_url, api_key, timeout,
           tuple(sorted(headers.items())))
    return _get_or_create(
        key,
        lambda: AsyncOpenAI(base_url=base_url, api_key=api_key,
                            timeout=timeout, default_headers=headers),
    )


def get_shared_groq_client(api_key: str, timeout: float = 30.0):
    """Process-wide ``AsyncGroq`` client, keyed by (api_key, timeout).
    Used by ``_call_groq`` (emergency fallback), which used to build
    one per call. Lazy ``groq`` import, as before — deploys without
    the SDK installed still import this module. Never close.
    """
    from groq import AsyncGroq

    timeout = float(timeout)
    return _get_or_create(
        ("groq", api_key, timeout),
        lambda: AsyncGroq(api_key=api_key, timeout=timeout),
    )


async def aclose_shared_clients() -> None:
    """Close every cached client. Tests / graceful shutdown only —
    production code never calls this (the pool lives with the
    process). Safe to call when no clients are cached.
    """
    entries = list(_CLIENTS.values())
    _CLIENTS.clear()
    for _loop, client in entries:
        try:
            await client.aclose()
        except Exception as e:  # noqa: BLE001 — best-effort teardown
            logger.debug("llm/_http: aclose_shared_clients skipped one: %r", e)
