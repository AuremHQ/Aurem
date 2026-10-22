"""tests/test_regression_iter2026_10q_ora_chat_providers_usage_ledger.py

Closing-the-bypass-doors audit — Step 3.

`services/ora_chat/providers.py` made its own raw OpenRouter calls
(`stream_call`/`one_shot`) with ZERO usage logging — the single
biggest unmetered LLM path found in the audit, reused by chat
(routers/ora_chat.py), deep research, the hallucination classifier,
both independent verifiers, scaffold design review, and adversarial
review. `one_shot` is the one every real caller actually uses (grep
confirms `stream_call` has no production caller today, only
`one_shot` does) — both are now wired, with a character-length
fallback estimate (marked `estimated=True`) when OpenRouter doesn't
return a `usage` object.

Uses `respx` (already a dev dependency — confirmed via the existing
pytest plugin list, no new dependency added) to mock the OpenRouter
HTTP endpoint and assert real behavior: what actually gets written to
`services.loop_token_ledger.log_real_usage`, not just a static grep.
"""
from __future__ import annotations

import httpx
import pytest
import respx

pytestmark = pytest.mark.asyncio

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-fake")


class TestOneShotUsageLedger:
    async def test_real_usage_logged_not_estimated(self, monkeypatch):
        from services.ora_chat import providers

        calls = []
        async def fake_log(model, usage, **kw):
            calls.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )

        with respx.mock:
            respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(
                200, json={
                    "choices": [{"message": {"content": "hello there"}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 10,
                              "total_tokens": 60, "cost": 0.0004},
                },
            ))
            content, _usage, err = await providers.one_shot(
                model="openrouter/some-model", messages=[{"role": "user", "content": "hi"}],
                temperature=0.5, top_p=1.0, presence_penalty=0.0, max_tokens=200,
                feature="Hallucination Classifier",
            )
        assert err is None
        assert content == "hello there"
        assert len(calls) == 1
        model, logged_usage, kw = calls[0]
        assert model == "openrouter/some-model"
        assert logged_usage["input_tokens"] == 50
        assert logged_usage["output_tokens"] == 10
        assert kw["estimated"] is False
        assert kw["feature"] == "Hallucination Classifier"
        assert kw["provider"] == "openrouter"

    async def test_missing_usage_falls_back_to_estimate(self, monkeypatch):
        from services.ora_chat import providers

        calls = []
        async def fake_log(model, usage, **kw):
            calls.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )

        with respx.mock:
            respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(
                200, json={
                    "choices": [{"message": {"content": "a" * 40}}],
                    # no "usage" key at all
                },
            ))
            _content, _usage, err = await providers.one_shot(
                model="openrouter/some-model", messages=[{"role": "user", "content": "x" * 20}],
                temperature=0.5, top_p=1.0, presence_penalty=0.0, max_tokens=200,
            )
        assert err is None
        assert len(calls) == 1
        _, logged_usage, kw = calls[0]
        assert kw["estimated"] is True, "no usage object -> must be marked estimated"
        assert logged_usage["output_tokens"] > 0, "estimate must be derived from real content length"

    async def test_http_error_logs_failed_status_not_bogus_cost(self, monkeypatch):
        from services.ora_chat import providers

        calls = []
        async def fake_log(model, usage, **kw):
            calls.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )

        with respx.mock:
            respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(
                429, text="rate limited",
            ))
            _content, _usage, err = await providers.one_shot(
                model="openrouter/some-model", messages=[{"role": "user", "content": "hi"}],
                temperature=0.5, top_p=1.0, presence_penalty=0.0, max_tokens=200,
            )
        assert err is not None
        assert len(calls) == 1
        _, logged_usage, kw = calls[0]
        assert kw["status"] == "failed"
        assert kw["error_reason"] == "http_429"
        assert logged_usage == {}, "a provider error must never fabricate a cost"


class TestStreamCallUsageLedger:
    def _sse_body(self, *, usage_chunk: bool) -> bytes:
        lines = [
            'data: {"choices": [{"delta": {"content": "hi "}}]}',
            'data: {"choices": [{"delta": {"content": "there"}}]}',
        ]
        if usage_chunk:
            lines.append(
                'data: {"choices": [], "usage": {"prompt_tokens": 30, '
                '"completion_tokens": 8, "total_tokens": 38}}'
            )
        lines.append("data: [DONE]")
        return ("\n".join(lines) + "\n").encode()

    async def test_real_usage_chunk_logged_not_estimated(self, monkeypatch):
        from services.ora_chat import providers

        calls = []
        async def fake_log(model, usage, **kw):
            calls.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )

        with respx.mock:
            respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(
                200, content=self._sse_body(usage_chunk=True),
                headers={"content-type": "text/event-stream"},
            ))
            events = [ev async for ev in providers.stream_call(
                model="openrouter/some-model",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.5, top_p=1.0, presence_penalty=0.0, max_tokens=200,
                feature="Deep Research",
            )]
        assert any(e["type"] == "done" for e in events)
        assert len(calls) == 1
        _, logged_usage, kw = calls[0]
        assert logged_usage["input_tokens"] == 30
        assert logged_usage["output_tokens"] == 8
        assert kw["estimated"] is False
        assert kw["feature"] == "Deep Research"

    async def test_no_usage_chunk_falls_back_to_estimate(self, monkeypatch):
        from services.ora_chat import providers

        calls = []
        async def fake_log(model, usage, **kw):
            calls.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )

        with respx.mock:
            respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(
                200, content=self._sse_body(usage_chunk=False),
                headers={"content-type": "text/event-stream"},
            ))
            events = [ev async for ev in providers.stream_call(
                model="openrouter/some-model",
                messages=[{"role": "user", "content": "x" * 40}],
                temperature=0.5, top_p=1.0, presence_penalty=0.0, max_tokens=200,
            )]
        assert any(e["type"] == "done" for e in events)
        assert len(calls) == 1
        _, logged_usage, kw = calls[0]
        assert kw["estimated"] is True
        assert logged_usage["output_tokens"] > 0, "hi there got real content, must estimate > 0"

    async def test_timeout_logs_failed_never_breaks_the_stream(self, monkeypatch):
        from services.ora_chat import providers

        calls = []
        async def fake_log(model, usage, **kw):
            calls.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )

        with respx.mock:
            respx.post(OPENROUTER_URL).mock(side_effect=httpx.TimeoutException("boom"))
            events = [ev async for ev in providers.stream_call(
                model="openrouter/some-model",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.5, top_p=1.0, presence_penalty=0.0, max_tokens=200,
            )]
        assert events[0]["type"] == "error"
        assert events[0]["error"] == "timeout"
        assert len(calls) == 1
        _, logged_usage, kw = calls[0]
        assert kw["status"] == "failed"
        assert kw["error_reason"] == "timeout"
        assert logged_usage == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
