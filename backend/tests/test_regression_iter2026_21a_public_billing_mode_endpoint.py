"""tests/test_regression_iter2026_21a_public_billing_mode_endpoint.py

ITEM 5(a)/5(d) (Medium) — independent-review fix round.

Facts: frontend VITE_TOKEN_BILLING_MODE is a BUILD-TIME constant;
backend TOKEN_BILLING_MODE is a RUNTIME env var. If the backend flips
to "on" without a matching frontend rebuild+redeploy, users see
task-wording UI while actually being billed in tokens. A small
public, read-only, rate-limited endpoint lets the frontend compare
its own build-time constant against the backend's live value and
detect the drift (ITEM 5(b), frontend-side, is a separate file).
"""
from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


class TestBillingModeEndpoint:
    async def test_returns_off_by_default(self, monkeypatch):
        from routers import payments
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)
        result = await payments.get_billing_mode_public(request=_FakeRequest())
        assert result == {"mode": "off"}

    async def test_returns_shadow(self, monkeypatch):
        from routers import payments
        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        result = await payments.get_billing_mode_public(request=_FakeRequest())
        assert result == {"mode": "shadow"}

    async def test_returns_on(self, monkeypatch):
        from routers import payments
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        result = await payments.get_billing_mode_public(request=_FakeRequest())
        assert result == {"mode": "on"}

    async def test_unknown_value_fails_safe_to_off(self, monkeypatch):
        from routers import payments
        monkeypatch.setenv("TOKEN_BILLING_MODE", "totally-not-a-real-mode")
        result = await payments.get_billing_mode_public(request=_FakeRequest())
        assert result == {"mode": "off"}

    async def test_response_has_no_extra_fields_or_secrets(self, monkeypatch):
        from routers import payments
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        result = await payments.get_billing_mode_public(request=_FakeRequest())
        assert set(result.keys()) == {"mode"}

    async def test_response_is_cacheable_for_a_few_seconds(self, monkeypatch):
        from fastapi import Response

        from routers import payments
        monkeypatch.setenv("TOKEN_BILLING_MODE", "off")
        response = Response()
        await payments.get_billing_mode_public(request=_FakeRequest(), response=response)
        assert response.headers.get("Cache-Control") == "public, max-age=10"

    async def test_rate_limited_per_ip(self, monkeypatch):
        from routers import payments
        monkeypatch.setattr(
            payments, "check_rate_limit_async",
            _AsyncReturns(False),
        )
        with pytest.raises(HTTPException) as exc_info:
            await payments.get_billing_mode_public(request=_FakeRequest())
        assert exc_info.value.status_code == 429


class _FakeRequest:
    client = type("c", (), {"host": "203.0.113.5"})()
    headers: ClassVar[dict] = {}


class _AsyncReturns:
    def __init__(self, value):
        self._value = value

    async def __call__(self, *_a, **_kw):
        return self._value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
