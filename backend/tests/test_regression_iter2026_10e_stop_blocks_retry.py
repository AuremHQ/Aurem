"""Regression — 2026-10 ORA chat/fix reliability follow-up.

Pins the invariant the silent-auto-retry loop in stream.py's gen()
(routers/chat/stream.py ~848-923) MUST uphold: if the client
disconnects / presses Stop WHILE attempt 1 is being silently
suppressed (asyncio.CancelledError/GeneratorExit raised out of the
`async for _chunk in consume_worker_queue(...)` at stream.py:850),
the `except (asyncio.CancelledError, GeneratorExit): ... raise` at
stream.py:855-893 re-raises immediately — attempt 2 (the retry) must
NEVER start. Without that `raise` (e.g. if a future edit swallowed
the exception before the `if not state.zero_tool_timeout_suppressed:
break` check at stream.py:894), a disconnect during the suppressed
attempt would silently fall through into starting a second LLM call
nobody is listening for.

Monkeypatches `consume_worker_queue` itself (not the LLM) so the
exact moment of cancellation — immediately after it flags a
suppressed zero-tool timeout, before yielding anything — is
deterministic rather than racing real timers.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import routers.chat as chat_mod
from main import app
from tests._stream_golden_harness import install_common_mocks, make_fake_resolve_pre_llm


def test_regression_iter2026_10e_stop_during_suppressed_attempt_blocks_retry(monkeypatch):
    install_common_mocks(monkeypatch)
    monkeypatch.setattr(chat_mod.stream, "classify_intent", lambda *a, **k: "A")
    monkeypatch.setattr(
        "services.ora_council_retriever.get_council_few_shot",
        lambda *a, **k: _council_result(),
    )
    monkeypatch.setattr(chat_mod.stream, "get_repo_context",
                         lambda *a, **k: _empty_str_coro())
    monkeypatch.setattr(
        "routers.chat_pre_llm.resolve_pre_llm",
        make_fake_resolve_pre_llm(tier="agentic", mode="A"),
    )

    calls = {"n": 0}

    async def _fake_consume_worker_queue(state, worker_t, ticker_t,
                                          suppress_zero_tool_timeout=False):
        calls["n"] += 1
        # Mirrors the real suppress branch's own bookkeeping
        # (watchdog.py:114-115,134-137) before the simulated
        # disconnect — proves the retry loop's decision to re-raise
        # instead of looping is reached via the REAL post-conditions
        # a suppressed attempt leaves behind.
        state.zero_tool_timeout_suppressed = True
        state.timed_out = True
        worker_t.cancel()
        ticker_t.cancel()
        raise asyncio.CancelledError("client disconnected mid-suppressed-attempt")
        yield  # pragma: no cover — unreachable, keeps this an async generator

    monkeypatch.setattr(chat_mod.stream, "consume_worker_queue",
                         _fake_consume_worker_queue)

    client = TestClient(app)
    try:
        client.post(
            "/api/aurem-dev/chat/stream",
            headers={"Authorization": "Bearer golden"},
            json={"project_id": "home", "session_id": "golden-sess-1",
                  "max_tool_iters": 2, "prompt": "do a deep multi-file audit please"},
        )
    except Exception:
        # The ASGI generator raising CancelledError mid-stream can
        # surface as a transport-level exception to the test client
        # depending on httpx/anyio version — irrelevant to what this
        # test pins. Only the call count below matters.
        pass

    assert calls["n"] == 1, (
        "a disconnect/Stop during the suppressed attempt must block the "
        "retry — consume_worker_queue must NOT be called a second time"
    )


async def _empty_str_coro():
    return ""


async def _council_result():
    return "", 0
