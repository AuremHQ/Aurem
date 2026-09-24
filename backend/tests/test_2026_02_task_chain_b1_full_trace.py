"""
tests/test_2026_02_task_chain_b1_full_trace.py — Part B1 regression test.

Before this fix, `log_task_chain` stripped every tool call down to
just its NAME before persisting to `task_chains` — args, the real
result (ok/error), and any timestamp were discarded. This meant a
"verified via X" claim (Part B2) could never actually be checked
against a real trace, because the trace itself didn't carry enough
detail to prove or disprove the claim.
"""
import time

import pytest
from unittest.mock import AsyncMock

from services.task_chain_logging import log_task_chain, get_turn_tool_records


class _FakeCollection:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)


class _FakeDB:
    def __init__(self):
        self.task_chains = _FakeCollection()


@pytest.mark.asyncio
async def test_tool_call_records_carry_name_args_result_timestamp_turn_id():
    db = _FakeDB()
    tool_calls = [
        {"tool": "read_repo_file", "args": {"path": "backend/app.py"},
         "ok": True, "error": None, "elapsed_ms": 42.5, "timestamp": time.time()},
        {"tool": "write_repo_file", "args": {"path": "backend/app.py", "content": "x"},
         "ok": False, "error": "write failed", "elapsed_ms": 10.0, "timestamp": time.time()},
    ]
    await log_task_chain(
        db, user_id="u1", session_id="s1", project_id="p1",
        user_prompt="fix the bug", tool_calls=tool_calls,
        final_response="Done", turn_state="success",
    )
    assert len(db.task_chains.inserted) == 1
    record = db.task_chains.inserted[0]

    # Pre-existing fields untouched (back-compat with /admin/failure-patterns).
    assert record["tool_calls"] == ["read_repo_file", "write_repo_file"]
    assert record["tool_sequence"] == ["read_repo_file", "write_repo_file"]

    # New: full per-call trace actually persisted.
    recs = record["tool_call_records"]
    assert len(recs) == 2
    assert recs[0]["tool"] == "read_repo_file"
    assert recs[0]["ok"] is True
    assert "backend/app.py" in recs[0]["args"]
    assert recs[0]["elapsed_ms"] == 42.5
    assert recs[0]["timestamp"] is not None
    assert recs[1]["tool"] == "write_repo_file"
    assert recs[1]["ok"] is False
    assert recs[1]["error"] == "write failed"
    # every record in the same turn shares one turn_id
    assert recs[0]["turn_id"] == recs[1]["turn_id"] == record["turn_id"]


@pytest.mark.asyncio
async def test_tool_call_records_redact_secrets_in_args():
    db = _FakeDB()
    tool_calls = [{
        "tool": "execute_bash", "args": {"cmd": "export TOKEN=ghp_" + "a" * 36},
        "ok": True, "error": None, "elapsed_ms": 5.0, "timestamp": time.time(),
    }]
    await log_task_chain(
        db, user_id="u1", session_id="s1", project_id="p1",
        user_prompt="run this", tool_calls=tool_calls,
        final_response="ran it", turn_state="success",
    )
    args_str = db.task_chains.inserted[0]["tool_call_records"][0]["args"]
    assert "ghp_" + "a" * 36 not in args_str
    assert "REDACTED" in args_str


def test_get_turn_tool_records_returns_lowercase_name_set():
    tool_calls = [
        {"tool": "read_repo_file"}, {"tool": "Write_Repo_File"},
    ]
    assert get_turn_tool_records(tool_calls) == {"read_repo_file", "write_repo_file"}


def test_get_turn_tool_records_empty_for_no_tool_calls():
    assert get_turn_tool_records(None) == set()
    assert get_turn_tool_records([]) == set()
