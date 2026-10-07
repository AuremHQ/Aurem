"""
Tool-failure handling found by the ORA chat reliability replay (turn 3 and
the "GitHub rejected the App installation" message for an LLM-key problem).
"""
from unittest.mock import AsyncMock, patch

import pytest

from services import error_translator
from services.tool_retry import should_retry


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    "OpenRouter HTTP 401: No auth credentials found",
    "Client error '401 Unauthorized' for url 'https://openrouter.ai/api/v1/chat/completions'",
    'LLM HTTP 401: {"error":{"message":"Missing Authentication header","code":401}}',
    "OPENROUTER_API_KEY not set",
])
async def test_regression_iter412_llm_provider_auth_is_never_shown_as_github(raw):
    out = await error_translator.translate(raw)
    assert out["source"] == "static_table"
    assert "AI model provider" in out["plain"]
    assert "App installation" not in out["plain"]
    assert not any("Installed GitHub Apps" in s for s in out["steps"])


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    "invalid_token (401 bad credentials)",
    "GitHub API 401 Bad credentials",
    "GET https://api.github.com/repos/o/r/contents/x 401",
    "app_installation_revoked",
])
async def test_regression_iter412_real_github_auth_errors_still_say_github(raw):
    out = await error_translator.translate(raw)
    assert "GitHub rejected the App installation" in out["plain"]


@pytest.mark.asyncio
async def test_regression_iter412_bare_401_with_no_source_is_not_blamed_on_github():
    out = await error_translator.translate("request failed with 401")
    assert "GitHub rejected" not in out["plain"]


@pytest.mark.parametrize("tool,res,expected", [
    ("list_issues", {"ok": False, "error": "GitHub API 502 Bad Gateway"}, True),
    ("read_repo_file", {"ok": False, "error": "connection reset by peer"}, True),
    ("search_repo", {"ok": False, "error": "rate limit exceeded (429)"}, True),
    ("list_issues", {"ok": False, "error": "401 Bad credentials"}, False),
    ("read_repo_file", {"ok": False, "error": "404 Not Found"}, False),
    ("read_repo_file", {"ok": False, "error": "502", "timed_out": True}, False),
    ("list_issues", {"ok": True}, False),
    ("write_repo_file", {"ok": False, "error": "502 Bad Gateway"}, False),   # never repeat a write
    ("patch_repo_file", {"ok": False, "error": "connection reset"}, False),
    ("list_issues", None, False),
])
def test_regression_iter412_should_retry_matrix(tool, res, expected):
    assert should_retry(tool, res) is expected


async def _run_turn(first, second):
    from services.orchestrator import chat_with_tools
    calls = {"llm": 0}

    async def fake_llm(*a, **k):
        calls["llm"] += 1
        return {"ok": True, "provider": "t", "content": "done" if calls["llm"] > 1 else "go",
                "fallback_chain": ["t"], "mode": "chat"}

    def fake_extract(content):
        return [{"tool": "list_issues", "args": {}}] if content == "go" else []

    local = AsyncMock(side_effect=[first, second])
    with patch("services.orchestrator.call_llm_with_meta", new=AsyncMock(side_effect=fake_llm)), \
         patch("services.orchestrator.list_tools", new=AsyncMock(return_value=[])), \
         patch("services.orchestrator.extract_tool_calls", side_effect=fake_extract), \
         patch("services.orchestrator.invoke_local_tool", new=local), \
         patch("services.tool_retry.RETRY_DELAY_S", 0):
        result = await chat_with_tools(prompt="show me issues", jwt_token="x",
                                       user_id="u", project_id="p", max_iters=4)
    return result, local


@pytest.mark.asyncio
async def test_regression_iter412_transient_failure_is_retried_once_and_recovers():
    result, local = await _run_turn({"ok": False, "error": "GitHub API 502 Bad Gateway"},
                                    {"ok": True, "result": "3 issues"})
    inv = result["tool_invocations"][0]
    assert local.await_count == 2
    assert inv["ok"] is True and inv["retried"] is True


@pytest.mark.asyncio
async def test_regression_iter412_auth_failure_is_not_retried():
    result, local = await _run_turn({"ok": False, "error": "401 Bad credentials"},
                                    {"ok": True, "result": "should not be called"})
    assert local.await_count == 1
    assert result["tool_invocations"][0]["ok"] is False
