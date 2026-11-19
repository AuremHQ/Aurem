# No-tool turns must not pay for the tool-use template.
# When the sliced catalog is empty, _TOOL_HELP_TEMPLATE only teaches
# the call format, so it is pure overhead on that turn.

import pytest

from services.orchestrator import _TOOL_HELP_TEMPLATE, _chat_with_tools_build_base_system

NO_TOOLS_CATALOG = "(no tools available - answer from your own knowledge)"


@pytest.mark.asyncio
async def test_no_tools_first_iter_system_omits_tool_help():
    first, followup = await _chat_with_tools_build_base_system("what is Aurem?", "", [], NO_TOOLS_CATALOG, None, [])
    assert _TOOL_HELP_TEMPLATE not in first
    assert _TOOL_HELP_TEMPLATE[:60] not in first
    assert "no tools available" in first
    assert _TOOL_HELP_TEMPLATE not in followup


@pytest.mark.asyncio
async def test_with_tools_first_iter_system_keeps_tool_help():
    tools = [{"name": "search_repo", "description": "search the repo", "args_spec": {"q": "str"}, "returns": "hits"}]
    first, _followup = await _chat_with_tools_build_base_system("find the login bug", "", [], "- search_repo: search the repo", None, tools)
    assert _TOOL_HELP_TEMPLATE in first
    assert "search_repo" in first
