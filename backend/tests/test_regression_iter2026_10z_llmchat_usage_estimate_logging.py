"""tests/test_regression_iter2026_10z_llmchat_usage_estimate_logging.py

Master Prompt v2 Part 1.2 — the remaining unmetered LLM paths that
use `emergentintegrations.llm.chat.LlmChat` (visual_bugfix_vision.py,
visual_bugfix_patch.py, faithfulness_judge.py, founder_summary.py,
routers/onboarding.py's demo-project fix) all had ZERO usage logging.

Confirmed by reading the installed library's own source
(`LlmChat.send_message` returns a plain `str`, discarding the
underlying completion response after extracting just the text) that
NONE of these clients expose a real usage object through any public
API — unlike the raw-OpenRouter paths (vision_describe.py,
advisor_vision.py, mermaid_diagram.py, ora_chat/providers.py), which
DO get a real `usage` object and only estimate when one happens to be
missing. So every call here is `estimated=True` on success, by
necessity, via the shared `services.loop_token_ledger.
log_estimated_usage` helper — never a fabricated cost on failure.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


class TestLogEstimatedUsageHelper:
    async def test_success_logs_estimated_true_with_nonzero_tokens(self, monkeypatch):
        from services.loop_token_ledger import log_estimated_usage

        calls = []
        async def fake_log(model, usage, **kw):
            calls.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )
        await log_estimated_usage(
            "anthropic/claude-sonnet-4-6", feature="Test Feature", provider="emergent",
            input_text="x" * 400, output_text="y" * 100,
        )
        assert len(calls) == 1
        _model, usage, kw = calls[0]
        assert kw["estimated"] is True
        assert kw["status"] == "ok"
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 25

    async def test_failure_logs_zero_usage_not_a_fabricated_cost(self, monkeypatch):
        from services.loop_token_ledger import log_estimated_usage

        calls = []
        async def fake_log(model, usage, **kw):
            calls.append((model, usage, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_real_usage", fake_log,
        )
        await log_estimated_usage(
            "anthropic/claude-sonnet-4-6", feature="Test Feature", provider="emergent",
            status="failed", error_reason="boom",
        )
        assert len(calls) == 1
        _, usage, kw = calls[0]
        assert usage == {}
        assert kw["status"] == "failed"
        assert kw["error_reason"] == "boom"


class _FakeLlmChat:
    """Mimics the fluent LlmChat(...).with_model(...) construction."""
    def __init__(self, reply=None, raises=None):
        self._reply = reply
        self._raises = raises

    def with_model(self, *a, **kw):
        return self

    async def send_message(self, *a, **kw):
        if self._raises:
            raise self._raises
        return self._reply


def _patch_llmchat(monkeypatch_target: str, chat: _FakeLlmChat):
    return patch(monkeypatch_target, return_value=chat)


class TestVisualBugfixVisionUsageLogging:
    async def test_success_logs_estimated_usage(self, monkeypatch):
        from services import visual_bugfix_vision as vbv

        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")
        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )
        reply = ('{"visual_defects": ["x"], "extracted_strings": [], '
                 '"inferred_dom_elements": [], "suggested_patterns": []}')
        with _patch_llmchat(
            "emergentintegrations.llm.chat.LlmChat", _FakeLlmChat(reply=reply),
        ):
            res = await vbv.analyze_screenshot(b"\x89PNG", "image/png", "button broken")
        assert res["ok"] is True
        assert len(calls) == 1
        _model, feature, kw = calls[0]
        assert feature == "Visual Bugfix Vision"
        assert kw["output_text"] == reply
        assert kw.get("status", "ok") == "ok"

    async def test_failure_logs_failed_status(self, monkeypatch):
        from services import visual_bugfix_vision as vbv

        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")
        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )
        with _patch_llmchat(
            "emergentintegrations.llm.chat.LlmChat",
            _FakeLlmChat(raises=RuntimeError("down")),
        ):
            res = await vbv.analyze_screenshot(b"\x89PNG", "image/png", "x")
        assert res["ok"] is False
        assert len(calls) == 1
        _, _, kw = calls[0]
        assert kw["status"] == "failed"


class TestVisualBugfixPatchUsageLogging:
    async def test_success_logs_estimated_usage(self, monkeypatch):
        from services import visual_bugfix_patch as vbp

        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")
        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )

        async def fake_read_repo_files(ctx, args):
            return {"files": [{"path": "a.py", "content": "x = 1", "ok": True}]}
        monkeypatch.setattr(
            "services.local_tools.read_repo_files", fake_read_repo_files,
        )
        import json as _json
        reply = _json.dumps({
            "summary": "fixed", "commit_message": "fix",
            "files": [{"path": "a.py", "new_content": "x = 2"}],
        })
        with _patch_llmchat(
            "emergentintegrations.llm.chat.LlmChat", _FakeLlmChat(reply=reply),
        ):
            res = await vbp.generate_patch(
                candidate_files=[{"path": "a.py"}],
                vision_analysis={"visual_defects": [], "extracted_strings": [],
                                 "inferred_dom_elements": []},
                bug_description="x", refine_instruction="", ctx={},
            )
        assert res["ok"] is True, res
        assert len(calls) == 1
        _, feature, kw = calls[0]
        assert feature == "Visual Bugfix Patch"
        assert kw["output_text"] == reply

    async def test_failure_logs_failed_status(self, monkeypatch):
        from services import visual_bugfix_patch as vbp

        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")
        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )

        async def fake_read_repo_files(ctx, args):
            return {"files": [{"path": "a.py", "content": "x = 1", "ok": True}]}
        monkeypatch.setattr(
            "services.local_tools.read_repo_files", fake_read_repo_files,
        )
        with _patch_llmchat(
            "emergentintegrations.llm.chat.LlmChat",
            _FakeLlmChat(raises=RuntimeError("down")),
        ):
            res = await vbp.generate_patch(
                candidate_files=[{"path": "a.py"}],
                vision_analysis={"visual_defects": []},
                bug_description="x", refine_instruction="", ctx={},
            )
        assert res["ok"] is False
        assert len(calls) == 1
        _, _, kw = calls[0]
        assert kw["status"] == "failed"


class TestFaithfulnessJudgeUsageLogging:
    async def test_success_logs_estimated_usage(self, monkeypatch):
        from services import faithfulness_judge as fj

        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")
        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )
        reply = '{"verdict": "faithful", "unsupported_claims": [], "reasoning": "ok"}'
        with _patch_llmchat(
            "emergentintegrations.llm.chat.LlmChat", _FakeLlmChat(reply=reply),
        ):
            res = await fj.llm_faithfulness_check(
                output="the output text", source="the source text",
            )
        assert res["ok"] is True
        assert len(calls) == 1
        _, feature, kw = calls[0]
        assert feature == "Faithfulness Judge"
        assert kw["output_text"] == reply

    async def test_failure_logs_failed_status(self, monkeypatch):
        from services import faithfulness_judge as fj

        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")
        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )
        with _patch_llmchat(
            "emergentintegrations.llm.chat.LlmChat",
            _FakeLlmChat(raises=RuntimeError("down")),
        ):
            res = await fj.llm_faithfulness_check(output="x", source="y")
        assert res["ok"] is False
        assert len(calls) == 1
        _, _, kw = calls[0]
        assert kw["status"] == "failed"


class TestFounderSummaryUsageLogging:
    async def test_success_logs_estimated_usage(self, monkeypatch):
        from services import founder_summary as fs

        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")
        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )
        reply = ('{"what_changed": "x", "what_to_verify": "y", "risk": "low"}')
        with _patch_llmchat(
            "emergentintegrations.llm.chat.LlmChat", _FakeLlmChat(reply=reply),
        ):
            result = await fs.generate_founder_summary(
                db=None, event_id=None, source="test",
                technical_event={"detail": "x"},
            )
        assert len(calls) == 1
        _, feature, kw = calls[0]
        assert feature == "Founder Summary"
        assert kw["output_text"] == reply
        assert result["founder_view"]["what_changed"] == "x"

    async def test_failure_logs_failed_status_and_falls_back(self, monkeypatch):
        from services import founder_summary as fs

        monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")
        calls = []
        async def fake_log(model, *, feature, **kw):
            calls.append((model, feature, kw))
        monkeypatch.setattr(
            "services.loop_token_ledger.log_estimated_usage", fake_log,
        )
        with _patch_llmchat(
            "emergentintegrations.llm.chat.LlmChat",
            _FakeLlmChat(raises=RuntimeError("down")),
        ):
            result = await fs.generate_founder_summary(
                db=None, event_id=None, source="test",
                technical_event={"detail": "x"},
            )
        assert len(calls) == 1
        _, _, kw = calls[0]
        assert kw["status"] == "failed"
        assert "generation_error" in result["founder_view"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
