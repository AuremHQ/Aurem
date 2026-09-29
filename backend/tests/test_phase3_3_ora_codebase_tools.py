"""
test_phase3_3_ora_codebase_tools.py — 2026-02 · Phase 3.3

Before this fix, the ONLY tool catalog the live v2 engine exposed
(services/ora_chat_v2/tools.py) had no way to read real source files —
every code-related claim ORA made was a guess dressed up as knowledge.
This locks in the new read_source_file / find_source_files /
search_code_defs / get_repo_tree tools, wired to the existing
services/ora_chat/codebase_index module which reads straight off disk
(so it can never go stale in a way a cache invalidation bug could hide).
"""
from __future__ import annotations

import pytest

from services.ora_chat_v2 import tools


class _FakeDb:
    """These tools never touch the db — dispatch just needs something
    to pass through."""
    def __getattr__(self, name):
        raise AssertionError(f"codebase tools should not touch db.{name}")


@pytest.fixture
def db():
    return _FakeDb()


def test_new_tools_are_registered_in_the_schema_the_model_sees():
    names = {s["function"]["name"] for s in tools.TOOL_SCHEMAS}
    assert {"get_repo_tree", "find_source_files",
             "read_source_file", "search_code_defs"} <= names


@pytest.mark.asyncio
async def test_read_source_file_returns_the_real_current_content(db):
    # Read this very test file back through the tool — proves the tool
    # is reading LIVE off disk, not a stale/pre-baked snapshot.
    result = await tools.execute_tool(
        db, "read_source_file",
        {"path": "backend/tests/test_phase3_3_ora_codebase_tools.py"})
    assert result["ok"] is True
    assert "the ONLY tool catalog the live v2 engine exposed" in result["content"]


@pytest.mark.asyncio
async def test_read_source_file_blocks_path_traversal(db):
    result = await tools.execute_tool(
        db, "read_source_file", {"path": "../../etc/passwd"})
    assert result["ok"] is False
    assert result["error"] == "path_traversal_blocked"


@pytest.mark.asyncio
async def test_find_source_files_locates_this_new_tools_module(db):
    result = await tools.execute_tool(
        db, "find_source_files", {"pattern": "ora_chat_v2/tools.py"})
    assert any(p.endswith("services/ora_chat_v2/tools.py")
               for p in result["matches"])


@pytest.mark.asyncio
async def test_search_code_defs_finds_run_turn(db):
    result = await tools.execute_tool(db, "search_code_defs", {"name": "run_turn"})
    assert any(r["path"].endswith("ora_chat_v2/engine.py") for r in result["results"])


@pytest.mark.asyncio
async def test_get_repo_tree_lists_real_top_level_dirs(db):
    result = await tools.execute_tool(db, "get_repo_tree", {})
    assert "backend/" in result["tree"] or "backend" in result["tree"]


@pytest.mark.asyncio
async def test_undefined_tool_still_rejected(db):
    result = await tools.execute_tool(db, "delete_the_repo", {})
    assert result == {"error": "undefined_tool", "name": "delete_the_repo"}
