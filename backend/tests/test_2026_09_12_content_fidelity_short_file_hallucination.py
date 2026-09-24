"""
2026-09-12 — content-fidelity fix, live-bug regression.

Reproduces the EXACT scenario a founder independently verified on
production (commit c771f0bddc8cb016eafdba05536367db2e6bc2c3): a task
asked to append one specific line to a small README.md, but the
worker's generation replaced the entire file with generic "Project
Name / Installation" boilerplate containing none of the requested
content — and it slipped through because `_hallucination_reasons()`
used to skip any original file under 10 non-blank lines.

These tests exercise the REAL shared gate (`_run_hallucination_gate`
from services/cto_pipeline_steps.py), not just the pure helper, to
prove the full retry-then-abort pipeline now protects short files.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from services.cto_pipeline_steps import _run_hallucination_gate

_REAL_README = "# Aurem\n\nA small starter project."
_BAD_REWRITE = (
    "# Project Name\n\n"
    "## Installation\n\nRun `npm install` to get started.\n\n"
    "## License\n\nMIT\n"
)
_TASK = "Append exactly `<!-- ora-fresh-ship-check-20 -->` to the end of README.md. Nothing else."


@pytest.mark.asyncio
async def test_short_file_full_rewrite_now_caught_and_retried():
    """The live bug: a small (<10 non-blank-line) README fully
    replaced with unrelated boilerplate must trip the gate — it must
    NOT be silently allowed through as it was before this fix."""
    edits = {"README.md": _BAD_REWRITE}
    contents = {"README.md": _REAL_README}

    # Retry succeeds: model is re-shown the real file and produces a
    # faithful, minimal append this time.
    good_retry_reply = (
        "SUMMARY: append marker\n"
        "FILE: README.md\n```\n"
        + _REAL_README + "\n<!-- ora-fresh-ship-check-20 -->\n"
        "```\n"
    )
    with patch("services.cto_pipeline_steps.call_llm", new=AsyncMock(return_value=good_retry_reply)):
        new_edits, err = await _run_hallucination_gate(
            task_id="t1", edits=edits, contents=contents,
            user_msg=_TASK, ai_sys="system prompt",
        )

    assert err is None, f"expected the retry to succeed, got error: {err}"
    assert "ora-fresh-ship-check-20" in new_edits["README.md"]
    assert "# Aurem" in new_edits["README.md"]  # original content preserved


@pytest.mark.asyncio
async def test_short_file_still_bad_after_retry_blocks_the_commit():
    """If even the corrective retry keeps hallucinating, the gate must
    return an error (caller aborts — never silently commits wrong
    content). This is the safety net for the reported data-loss risk:
    when the AI can't produce a faithful edit, it must fail loudly,
    not ship something false."""
    edits = {"README.md": _BAD_REWRITE}
    contents = {"README.md": _REAL_README}

    # Retry ALSO produces an unrelated full rewrite.
    still_bad_reply = "SUMMARY: oops\nFILE: README.md\n```\n" + _BAD_REWRITE + "\n```\n"
    with patch("services.cto_pipeline_steps.call_llm", new=AsyncMock(return_value=still_bad_reply)):
        new_edits, err = await _run_hallucination_gate(
            task_id="t2", edits=edits, contents=contents,
            user_msg=_TASK, ai_sys="system prompt",
        )

    assert err is not None
    assert "README.md" in err


@pytest.mark.asyncio
async def test_faithful_small_edit_passes_without_any_retry():
    """A correct, minimal edit to a short file must pass straight
    through with zero retries (no false positives on legitimate
    small-file edits)."""
    edits = {"README.md": _REAL_README + "\n<!-- ora-fresh-ship-check-20 -->\n"}
    contents = {"README.md": _REAL_README}

    with patch("services.cto_pipeline_steps.call_llm", new=AsyncMock(side_effect=AssertionError("must not retry"))):
        new_edits, err = await _run_hallucination_gate(
            task_id="t3", edits=edits, contents=contents,
            user_msg=_TASK, ai_sys="system prompt",
        )

    assert err is None
    assert new_edits is edits


# ── Round 2 — the blank-line variant (live commit 2e74ea1, founder's
# "verify-3" repro). The original file is FULLY preserved this time
# (keep-ratio ~1.0, round-1's gate never fires on its own) but the
# specific "exactly `X`" literal the task required is missing/blank
# from the appended line. This is what `_verbatim_reasons` closes. ──

_BLANK_LINE_EDIT = _REAL_README + "\n\n"        # original intact, new line is BLANK


@pytest.mark.asyncio
async def test_blank_line_instead_of_literal_now_caught_and_retried():
    """Original content 100% preserved -> round-1 keep-ratio check
    alone would pass this straight through. The verbatim-literal
    check must still trip it, and a corrective retry that actually
    includes the exact literal must be accepted."""
    edits = {"README.md": _BLANK_LINE_EDIT}
    contents = {"README.md": _REAL_README}

    good_retry_reply = (
        "SUMMARY: append marker\nFILE: README.md\n```\n"
        + _REAL_README + "\n<!-- ora-fresh-ship-check-20 -->\n```\n"
    )
    with patch("services.cto_pipeline_steps.call_llm", new=AsyncMock(return_value=good_retry_reply)):
        new_edits, err = await _run_hallucination_gate(
            task_id="t4", edits=edits, contents=contents,
            user_msg=_TASK, ai_sys="system prompt",
        )

    assert err is None, f"expected the retry to succeed, got error: {err}"
    assert "ora-fresh-ship-check-20" in new_edits["README.md"]


@pytest.mark.asyncio
async def test_blank_line_still_missing_literal_after_retry_blocks_the_commit():
    """If the retry ALSO fails to include the required literal, the
    gate must abort rather than let a false 'verified' commit happen
    (this is the exact false-positive the founder caught live —
    the commit message claimed the literal was appended and the
    pipeline's own post-push verify said "Verified", but the file
    only had a blank line)."""
    edits = {"README.md": _BLANK_LINE_EDIT}
    contents = {"README.md": _REAL_README}

    still_blank_reply = "SUMMARY: oops\nFILE: README.md\n```\n" + _REAL_README + "\n\n```\n"
    with patch("services.cto_pipeline_steps.call_llm", new=AsyncMock(return_value=still_blank_reply)):
        new_edits, err = await _run_hallucination_gate(
            task_id="t5", edits=edits, contents=contents,
            user_msg=_TASK, ai_sys="system prompt",
        )

    assert err is not None
    assert "ora-fresh-ship-check-20" in err or "literal" in err.lower()


@pytest.mark.asyncio
async def test_task_without_exactly_phrasing_unaffected():
    """A task with no explicit "exactly `X`" literal must never trip
    the verbatim check — it's scoped strictly to that deliberate
    phrasing, not every insert/edit task."""
    edits = {"README.md": _REAL_README + "\nAdded a note.\n"}
    contents = {"README.md": _REAL_README}

    with patch("services.cto_pipeline_steps.call_llm", new=AsyncMock(side_effect=AssertionError("must not retry"))):
        new_edits, err = await _run_hallucination_gate(
            task_id="t6", edits=edits, contents=contents,
            user_msg="Add a short note to the end of README.md.", ai_sys="system prompt",
        )

    assert err is None
    assert new_edits is edits
