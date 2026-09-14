"""
tests/test_2026_02_self_bug_tool_error_wiring.py — Gap 1 (2026-02)

Founder finding (live, in /admin/self-bugs + production today):
dead_end_leak genuinely gets logged (verified: a real entry matched
a real earlier conversation), but TOOL_ERROR showed "No self-bugs
logged yet" despite 4 real tool-related incidents happening in the
same session. Root-cause (confirmed by grep across the whole repo
BEFORE writing any fix): `services/self_bug.emit()` is the one
shared write path into `ora_self_bugs`, and the read side
(`routers/self_bugs_admin.py`) reads that exact same collection with
a matching `type` filter — no read/write mismatch. The actual gap:
NOTHING in the codebase ever called `emit("tool_error", ...)` at
all. Not a matching-rules gap, not a silent write failure — the
classifier never ran on tool-call-related content, full stop.

Fix: `services/chat_helpers.py::apply_output_guards` — which already
runs on every reply, in both production call sites (turn.py,
stream.py) — now fires `self_bug.emit("tool_error", ...)` when its
two most-recent guards actually rewrite something:
  1. `internal_directive_guard.strip_leaked_tool_directives` — the
     self-instruction-leak incident ("STOP guessing paths...").
  2. `commit_boundary.strip_stray_code_blocks_near_handoff` — the
     destructive-diff-marked-clean incident (index.js "+2/-47").
Same shared `self_bug.emit` pipeline dead_end_leak already uses via
`business_voice_filter.py::apply_business_owner_guards` — one
pipeline, two new real trigger sites, not a second mechanism.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.chat_helpers import apply_output_guards


@pytest.mark.asyncio
async def test_internal_directive_leak_fires_tool_error_self_bug():
    leaked = (
        "❌ FILE NOT FOUND: `injection_scanner.py` does not exist on "
        "TJSNDHU/Aurem@main. STOP guessing paths. "
        "Your next tool call MUST be `list_repo_files` with a glob "
        "(e.g. `**/auth*.py`, `**/*router*.py`) to DISCOVER the "
        "real paths in this repo. Do not write a plan, do not "
        "produce a handoff brief, do not cite any file paths — "
        "until you have called list_repo_files and seen the "
        "actual layout. Most repos do not follow the layout you "
        "expect from training data."
    )
    with patch("services.self_bug.emit", new_callable=AsyncMock) as mock_emit:
        out = apply_output_guards("check validators.py line 88", leaked, False, "")
        # apply_output_guards fires self_bug via asyncio.create_task —
        # give the loop one tick to actually schedule/run it.
        await asyncio.sleep(0)
        assert "STOP guessing paths" not in out
        assert mock_emit.await_count >= 1
        call_args = mock_emit.await_args_list[0]
        assert call_args.args[0] == "tool_error"
        assert call_args.kwargs.get("source") == "internal_directive_guard"


@pytest.mark.asyncio
async def test_destructive_diff_marked_clean_fires_tool_error_self_bug():
    leaked = (
        "I added the comment.\n\n"
        "```js\nconst root = ReactDOM.createRoot(x);\nroot.render(<App />);\n```\n\n"
        "```aurem-handoff\n"
        "In frontend/src/index.js add the comment at the top of the file.\n"
        "```\n"
    )
    with patch("services.self_bug.emit", new_callable=AsyncMock) as mock_emit:
        out = apply_output_guards("add a comment to index.js", leaked, False, "", tool_calls_run=1)
        await asyncio.sleep(0)
        assert "```js" not in out
        assert mock_emit.await_count >= 1
        call_args = mock_emit.await_args_list[0]
        assert call_args.args[0] == "tool_error"
        assert call_args.kwargs.get("source") == "handoff_diff_safety_guard"


@pytest.mark.asyncio
async def test_no_false_positive_when_nothing_leaked():
    clean = "Here's a quick summary of what I found in validators.py."
    with patch("services.self_bug.emit", new_callable=AsyncMock) as mock_emit:
        apply_output_guards("check validators.py", clean, False, "")
        await asyncio.sleep(0)
        assert mock_emit.await_count == 0


@pytest.mark.asyncio
async def test_session_and_user_id_propagate_into_context():
    leaked = (
        "❌ FILE NOT FOUND: `exec_usage.py` does not exist on X/Y@main. "
        "STOP guessing paths. Your next tool call MUST be `list_repo_files` "
        "with a glob (e.g. `**/auth*.py`, `**/*router*.py`) to DISCOVER the "
        "real paths in this repo. Do not write a plan, do not "
        "produce a handoff brief, do not cite any file paths — "
        "until you have called list_repo_files and seen the "
        "actual layout. Most repos do not follow the layout you "
        "expect from training data."
    )
    with patch("services.self_bug.emit", new_callable=AsyncMock) as mock_emit:
        apply_output_guards(
            "check exec_usage", leaked, False, "",
            session_id="sess-abc-123", user_id="user-xyz-9",
        )
        await asyncio.sleep(0)
        assert mock_emit.await_count >= 1
        ctx = mock_emit.await_args_list[0].args[2]
        assert ctx["session_id"] == "sess-abc-123"
        assert ctx["user_id"] == "user-xyz-9"


def test_no_running_loop_never_raises():
    # apply_output_guards is called synchronously by several existing
    # tests with NO running event loop — asyncio.create_task() would
    # raise RuntimeError there. Must be swallowed, never break the
    # actual guard's return value.
    leaked = (
        "❌ FILE NOT FOUND: `x.py` does not exist on A/B@main. "
        "STOP guessing paths. Your next tool call MUST be `list_repo_files` "
        "with a glob (e.g. `**/auth*.py`, `**/*router*.py`) to DISCOVER the "
        "real paths in this repo. Do not write a plan, do not "
        "produce a handoff brief, do not cite any file paths — "
        "until you have called list_repo_files and seen the "
        "actual layout. Most repos do not follow the layout you "
        "expect from training data."
    )
    out = apply_output_guards("x", leaked, False, "")
    assert "STOP guessing paths" not in out


@pytest.mark.asyncio
async def test_source_specific_cause_is_known_not_invented():
    from services.self_bug import diagnose, SelfBugEvent
    d1 = diagnose(SelfBugEvent(
        type="tool_error", source="internal_directive_guard",
        evidence="STOP guessing paths...",
    ))
    assert "leaked" in d1.likely_cause
    assert d1.severity == "high"

    d2 = diagnose(SelfBugEvent(
        type="tool_error", source="handoff_diff_safety_guard",
        evidence="```js\n...",
    ))
    assert "diff" in d2.likely_cause
    assert d2.severity == "high"

    # An unrelated source for the same type still falls back to the
    # original generic (still-true) cause — never invents specificity
    # it doesn't actually have evidence for.
    d3 = diagnose(SelfBugEvent(type="tool_error", source="some_other_guard", evidence="x"))
    assert d3.likely_cause == "a tool call ORA made returned an error"
