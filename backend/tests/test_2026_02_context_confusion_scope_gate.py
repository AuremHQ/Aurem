"""
tests/test_2026_02_context_confusion_scope_gate.py — Part C of the
architectural "Context Confusion" fix (2026-02).

Per the founder's ticket: file-scope must be enforced IN CODE, not
trusted to model judgment alone, and BLOCKED at the tool-call layer
(pre-emptively, before write_repo_file/patch_repo_file ever run) —
not just flagged by the existing post-hoc scope-creep/hallucination
content-scanning gate that runs AFTER a diff is already generated.

These tests exercise the REAL `invoke_local_tool` dispatcher end to
end (not just the extracted helper functions), mocking only the
underlying `write_repo_file`/`read_repo_file` implementations inside
`LOCAL_TOOLS` so we can assert whether they were ever actually
CALLED — the real proof that a blocked write never reaches the tool.
"""
from unittest.mock import AsyncMock, patch

import pytest

from services.local_tools import invoke_local_tool, LOCAL_TOOLS


@pytest.mark.asyncio
async def test_write_outside_discovered_scope_is_blocked_before_the_tool_runs():
    ctx = {"user_prompt": "update the footer text to say hello", "scope_files": {"frontend/src/components/Footer.jsx"}}
    fake_write = AsyncMock(return_value={"ok": True, "data": {"ok": True, "committed": True}})
    with patch.dict(LOCAL_TOOLS, {"write_repo_file": fake_write}):
        out = await invoke_local_tool(
            "write_repo_file",
            {"path": "frontend/src/index.js", "content": "// unrelated file"},
            ctx,
        )
    assert out is not None
    assert out.get("ok") is False
    assert out.get("error_class") == "scope_blocked"
    fake_write.assert_not_called()   # the real proof: never reached the tool


@pytest.mark.asyncio
async def test_write_to_a_path_read_earlier_this_turn_is_allowed():
    ctx = {"user_prompt": "update the footer text", "scope_files": {"frontend/src/components/Footer.jsx"}}
    fake_write = AsyncMock(return_value={"ok": True, "committed": True})
    with patch.dict(LOCAL_TOOLS, {"write_repo_file": fake_write}):
        out = await invoke_local_tool(
            "write_repo_file",
            {"path": "frontend/src/components/Footer.jsx", "content": "new footer"},
            ctx,
        )
    fake_write.assert_awaited_once()
    assert out == {"ok": True, "committed": True}


@pytest.mark.asyncio
async def test_write_to_a_path_named_explicitly_in_the_prompt_is_allowed_with_no_prior_read():
    ctx = {"user_prompt": "add a comment to frontend/src/index.js", "scope_files": {"some/other/file.py"}}
    fake_write = AsyncMock(return_value={"ok": True})
    with patch.dict(LOCAL_TOOLS, {"write_repo_file": fake_write}):
        out = await invoke_local_tool(
            "write_repo_file", {"path": "frontend/src/index.js", "content": "x"}, ctx,
        )
    fake_write.assert_awaited_once()
    assert out == {"ok": True}


@pytest.mark.asyncio
async def test_write_with_completely_empty_scope_this_turn_is_not_blocked_no_regression():
    """No prior reads, no explicit path in the prompt — nothing to
    bound against yet, so this must proceed exactly as before this
    fix (preserves every existing single-shot-write flow)."""
    ctx = {"user_prompt": "fix the bug"}
    fake_write = AsyncMock(return_value={"ok": True})
    with patch.dict(LOCAL_TOOLS, {"write_repo_file": fake_write}):
        out = await invoke_local_tool(
            "write_repo_file", {"path": "any/file.py", "content": "x"}, ctx,
        )
    fake_write.assert_awaited_once()
    assert out == {"ok": True}


@pytest.mark.asyncio
async def test_a_successful_read_repo_file_call_adds_its_path_to_scope():
    ctx = {"user_prompt": "investigate this bug"}
    fake_read = AsyncMock(return_value={"ok": True, "content": "..."})
    with patch.dict(LOCAL_TOOLS, {"read_repo_file": fake_read}):
        await invoke_local_tool("read_repo_file", {"path": "backend/main.py"}, ctx)
    assert "backend/main.py" in ctx.get("scope_files", set())


@pytest.mark.asyncio
async def test_a_successful_list_repo_files_call_adds_every_listed_path_to_scope():
    ctx = {"user_prompt": "what files exist"}
    fake_list = AsyncMock(return_value={
        "ok": True, "files": [{"path": "a.py"}, {"path": "b.py"}],
    })
    with patch.dict(LOCAL_TOOLS, {"list_repo_files": fake_list}):
        await invoke_local_tool("list_repo_files", {}, ctx)
    assert {"a.py", "b.py"} <= ctx.get("scope_files", set())


@pytest.mark.asyncio
async def test_patch_repo_file_is_also_covered_not_just_write_repo_file():
    ctx = {"user_prompt": "patch it", "scope_files": {"known.py"}}
    fake_patch = AsyncMock(return_value={"ok": True})
    with patch.dict(LOCAL_TOOLS, {"patch_repo_file": fake_patch}):
        out = await invoke_local_tool(
            "patch_repo_file", {"path": "unknown.py", "diff": "..."}, ctx,
        )
    assert out.get("error_class") == "scope_blocked"
    fake_patch.assert_not_called()


@pytest.mark.asyncio
async def test_blocked_write_is_not_recorded_in_tool_calls_as_a_real_dispatch():
    """A blocked write must not pollute the CitationGuard's
    tool_calls trace as if it were a real, executed call."""
    ctx = {"user_prompt": "x", "scope_files": {"known.py"}}
    fake_write = AsyncMock(return_value={"ok": True})
    with patch.dict(LOCAL_TOOLS, {"write_repo_file": fake_write}):
        await invoke_local_tool("write_repo_file", {"path": "unknown.py"}, ctx)
    assert not any(c.get("tool") == "write_repo_file" for c in ctx.get("tool_calls", []))
