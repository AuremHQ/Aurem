"""tests/test_regression_iter2026_10r_vision_describe_usage_ledger.py

Closing-the-bypass-doors audit — Step 3.

`services/vision_describe.py` made a raw OpenRouter call (via
`services.http.ext_client`) with ZERO usage logging — one of the
confirmed bypasses in the A3 audit. Reuses the exact `httpx.AsyncClient`
monkeypatch pattern already established in
test_iter110_vision_and_council_fix.py (patching `up.httpx.AsyncClient`
also affects `ext_client`'s internal `httpx.AsyncClient(...)` call,
since it's the same shared module object).
"""
from __future__ import annotations

import asyncio

import pytest


class _MockResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def _mock_client(responses: list):
    calls = []

    class MockClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, headers=None, json=None):
            calls.append(json["model"])
            return responses.pop(0)

    return MockClient, calls


class TestVisionDescribeUsageLedger:
    def test_success_logs_real_usage(self, monkeypatch):
        from services import vision_describe as vd
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        logged = []
        async def fake_log(model, usage, **kw):
            logged.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )

        MockClient, _calls = _mock_client([_MockResp(200, {
            "choices": [{"message": {"content": "a red square"}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 40, "cost": 0.0009},
        })])
        monkeypatch.setattr(vd.httpx, "AsyncClient", MockClient)

        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        out = asyncio.run(vd.describe_image_via_vision(raw, "image/png", "x.png"))
        assert "red square" in out
        assert len(logged) == 1
        model, usage, kw = logged[0]
        assert model == vd._PRIMARY_VISION_MODEL
        assert usage["input_tokens"] == 500
        assert usage["output_tokens"] == 40
        assert kw["feature"] == "Vision Describe"
        assert kw["provider"] == "openrouter"
        assert kw["status"] == "ok"

    def test_http_error_logs_failed_not_a_fabricated_cost(self, monkeypatch):
        from services import vision_describe as vd
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        logged = []
        async def fake_log(model, usage, **kw):
            logged.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )

        # Primary fails (400), fallback also fails (500) — both calls
        # must each log their own failed row, no bogus cost either time.
        MockClient, _calls = _mock_client([
            _MockResp(400, {"error": "bad image"}),
            _MockResp(500, {"error": "boom"}),
        ])
        monkeypatch.setattr(vd.httpx, "AsyncClient", MockClient)

        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        out = asyncio.run(vd.describe_image_via_vision(raw, "image/png", "x.png"))
        assert out == ""
        assert len(logged) == 2
        for _model, usage, kw in logged:
            assert kw["status"] == "failed"
            assert usage == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
