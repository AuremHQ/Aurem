"""tests/test_regression_iter2026_10u_onboarding_demo_cost_breaker_gate.py

Closing-the-bypass-doors audit — Step 4.

`POST /onboarding/demo-project/run` (routers/onboarding.py) makes a
real `LlmChat` call (Claude Sonnet) to "fix" a canned demo bug, with
NO budget/tier check of any kind — any logged-in user could hit it
repeatedly at zero cost to themselves. Not a payment gate (the demo
is meant to be free) — same platform-wide `assert_within_cap` infra
breaker used for founder_offer.py's SEO giveaway.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


class _FakeDB:
    pass


class TestOnboardingDemoCostBreakerGate:
    async def test_cap_tripped_blocks_before_the_llm_call(self, monkeypatch):
        from routers import onboarding as ob

        async def fake_current_dev(auth=None):
            return {"user_id": "u1"}
        monkeypatch.setattr(ob, "current_dev", fake_current_dev)
        monkeypatch.setattr(ob, "get_db", lambda: _FakeDB())

        async def fake_emit_funnel_event(*a, **kw):
            return None
        monkeypatch.setattr(ob, "emit_funnel_event", fake_emit_funnel_event)

        async def fake_assert_within_cap(db, **kw):
            raise HTTPException(429, {"error": "llm_cost_cap_hit"})
        monkeypatch.setattr(
            "services.llm_cost_breaker.assert_within_cap", fake_assert_within_cap,
        )

        llm_called = {"yes": False}
        class _FakeLlmChat:
            def __init__(self, *a, **kw):
                llm_called["yes"] = True
            def with_model(self, *a, **kw):
                return self
            async def send_message(self, *a, **kw):
                return "should never be reached"
        import emergentintegrations.llm.chat as llmchat_mod
        monkeypatch.setattr(llmchat_mod, "LlmChat", _FakeLlmChat)

        with pytest.raises(HTTPException) as exc_info:
            await ob.run_demo_project(authorization="Bearer x")
        assert exc_info.value.status_code == 429
        assert llm_called["yes"] is False, (
            "the cap must block BEFORE the LLM call — no spend once "
            "the platform-wide breaker has tripped"
        )

    async def test_cap_clear_runs_as_before(self, monkeypatch):
        from routers import onboarding as ob

        async def fake_current_dev(auth=None):
            return {"user_id": "u1"}
        monkeypatch.setattr(ob, "current_dev", fake_current_dev)
        monkeypatch.setattr(ob, "get_db", lambda: _FakeDB())

        async def fake_emit_funnel_event(*a, **kw):
            return None
        monkeypatch.setattr(ob, "emit_funnel_event", fake_emit_funnel_event)

        async def fake_assert_within_cap(db, **kw):
            return None
        monkeypatch.setattr(
            "services.llm_cost_breaker.assert_within_cap", fake_assert_within_cap,
        )

        llm_called = {"yes": False}
        class _FakeLlmChat:
            def __init__(self, *a, **kw):
                llm_called["yes"] = True
            def with_model(self, *a, **kw):
                return self
            async def send_message(self, *a, **kw):
                import json
                return json.dumps({
                    "explanation": "fixed it",
                    "fixed_source": "def calculate_order_total(): return 1",
                })
        import emergentintegrations.llm.chat as llmchat_mod
        monkeypatch.setattr(llmchat_mod, "LlmChat", _FakeLlmChat)
        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")

        result = await ob.run_demo_project(authorization="Bearer x")
        assert llm_called["yes"] is True
        assert result.get("ok", True) is not False


class TestOnboardingDemoUsageLogging:
    """Master Prompt v2 Part 1.2 — this LlmChat call had zero usage
    logging. Uses the shared services.loop_token_ledger.
    log_estimated_usage helper (LlmChat exposes no real usage object
    through any public API — see test_regression_iter2026_10z's
    module docstring for the source-level confirmation)."""

    async def test_success_logs_estimated_usage(self, monkeypatch):
        from routers import onboarding as ob

        async def fake_current_dev(auth=None):
            return {"user_id": "u1"}
        monkeypatch.setattr(ob, "current_dev", fake_current_dev)
        monkeypatch.setattr(ob, "get_db", lambda: _FakeDB())

        async def fake_emit_funnel_event(*a, **kw):
            return None
        monkeypatch.setattr(ob, "emit_funnel_event", fake_emit_funnel_event)

        async def fake_assert_within_cap(db, **kw):
            return None
        monkeypatch.setattr(
            "services.llm_cost_breaker.assert_within_cap", fake_assert_within_cap,
        )

        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )

        reply = '{"explanation": "fixed it", "fixed_source": "def f(): return 1"}'
        class _FakeLlmChat:
            def __init__(self, *a, **kw):
                pass
            def with_model(self, *a, **kw):
                return self
            async def send_message(self, *a, **kw):
                return reply
        import emergentintegrations.llm.chat as llmchat_mod
        monkeypatch.setattr(llmchat_mod, "LlmChat", _FakeLlmChat)
        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")

        await ob.run_demo_project(authorization="Bearer x")
        assert len(calls) == 1
        _, feature, kw = calls[0]
        assert feature == "Onboarding Demo Fix"
        assert kw["output_text"] == reply
        assert kw.get("status", "ok") == "ok"

    async def test_llm_failure_logs_failed_status(self, monkeypatch):
        from routers import onboarding as ob

        async def fake_current_dev(auth=None):
            return {"user_id": "u1"}
        monkeypatch.setattr(ob, "current_dev", fake_current_dev)
        monkeypatch.setattr(ob, "get_db", lambda: _FakeDB())

        async def fake_emit_funnel_event(*a, **kw):
            return None
        monkeypatch.setattr(ob, "emit_funnel_event", fake_emit_funnel_event)

        async def fake_assert_within_cap(db, **kw):
            return None
        monkeypatch.setattr(
            "services.llm_cost_breaker.assert_within_cap", fake_assert_within_cap,
        )

        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )

        class _FakeLlmChat:
            def __init__(self, *a, **kw):
                pass
            def with_model(self, *a, **kw):
                return self
            async def send_message(self, *a, **kw):
                raise RuntimeError("down")
        import emergentintegrations.llm.chat as llmchat_mod
        monkeypatch.setattr(llmchat_mod, "LlmChat", _FakeLlmChat)
        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")

        # run_demo_project catches the LLM failure and falls back to
        # the known-good demo fix — must not raise.
        await ob.run_demo_project(authorization="Bearer x")
        assert len(calls) == 1
        _, _, kw = calls[0]
        assert kw["status"] == "failed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
