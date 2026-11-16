"""tests/test_regression_iter2026_69_local_tools_exports.py

Regression: services/local_tools.py was truncated by a bad merge
(4a8796a4: 3172 -> 1272 lines). The module lost TOOL_SPECS,
LOCAL_TOOLS, invoke_local_tool and search_repo, so services.orchestrator
(`from .local_tools import TOOL_SPECS ...`) and 83 test modules failed
at import/collection time, which blinded CI.

These tests pin the public surface of the module so a truncation
cannot merge unnoticed again.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

REQUIRED_TOOLS = {
    "read_repo_file", "read_repo_files", "list_repo_files", "search_repo",
    "write_repo_file", "execute_bash", "get_commit_diff", "get_repo_info",
    "save_finding",
}


def _lt():
    return importlib.import_module("services.local_tools")


class TestPublicSurface:
    def test_exports_used_by_orchestrator_exist(self):
        lt = _lt()
        for name in ("TOOL_SPECS", "LOCAL_TOOLS", "invoke_local_tool",
                     "_is_safe_repo_path"):
            assert hasattr(lt, name), f"local_tools lost `{name}`"

    def test_tool_specs_is_a_non_empty_list_of_dicts(self):
        specs = _lt().TOOL_SPECS
        assert isinstance(specs, list) and len(specs) >= len(REQUIRED_TOOLS)
        assert all(isinstance(s, dict) for s in specs)

    def test_every_required_tool_is_registered(self):
        assert REQUIRED_TOOLS <= set(_lt().LOCAL_TOOLS)

    def test_every_registered_tool_is_an_async_callable(self):
        for name, fn in _lt().LOCAL_TOOLS.items():
            assert callable(fn), name
            assert inspect.iscoroutinefunction(fn), f"{name} must be async"

    def test_every_spec_names_a_registered_tool(self):
        lt = _lt()
        def spec_name(s):
            return s.get("name") or s.get("function", {}).get("name")
        names = [spec_name(s) for s in lt.TOOL_SPECS]
        assert all(names), "a TOOL_SPECS entry has no name"
        missing = [n for n in names if n not in lt.LOCAL_TOOLS]
        assert not missing, f"specs without a tool: {missing}"

    def test_tool_spec_names_are_unique(self):
        names = [s.get("name") or s.get("function", {}).get("name")
                 for s in _lt().TOOL_SPECS]
        assert len(names) == len(set(names))


class TestFileIsNotTruncated:
    def test_module_is_not_a_stub_sized_file(self):
        src = inspect.getsource(_lt())
        assert src.count("\n") > 2500

    def test_no_function_body_is_spliced_with_another_tools_code(self):
        # The corrupt file ended execute_bash with a commit_files call.
        src = inspect.getsource(_lt().execute_bash)
        assert "commit_files" not in src

    def test_orchestrator_imports_cleanly(self):
        importlib.import_module("services.orchestrator")


@pytest.mark.asyncio
async def test_invoke_local_tool_returns_none_for_a_non_local_tool():
    # Documented contract: None means "not a local tool", so the
    # orchestrator falls through to the next tool source.
    assert await _lt().invoke_local_tool("no_such_tool", {}, {}) is None
