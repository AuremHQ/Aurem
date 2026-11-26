"""P1 — Part VI P1: surgical diff-hunk self-heal (ORA audit).

Audit claim (verify-latency-debt.md, CONFIRMED on main 13757d3f):
`loop_verify.self_heal()` asks the LLM for the COMPLETE file body
for every lint fix, so even a one-line fix rewrites the whole file
and the model can drift untouched lines.

Fix: `self_heal` first tries a unified-diff heal (one 600-token
call, applied programmatically via services/diff_hunk.py) and falls
back to the existing full-file rewrite on any failure.  These tests
drive both layers with a stub LLM — no provider, no Mongo, no GitHub.

Live-path caveat (same pattern as p1b's L5 note): on this HEAD the
Loop engine uses the Parliament healer (Iter 212m-150), not
`loop_verify.self_heal`, so this fix takes effect wherever
`self_heal` is called (it is still exported and directly tested).
The standalone `services/diff_hunk.py` applier is there for the
Parliament healer to adopt in a follow-up.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import loop_verify  # noqa: E402
from services.diff_hunk import apply_unified_diff  # noqa: E402


def _diff(*lines: str) -> str:
    return "\n".join(lines) + "\n"


# 10-line file, trailing newline.  Line numbers (1-based):
#  1 def add(a, b): | 2 return a + b | 3 "" | 4 "" | 5 def sub(a, b):
#  6 return a - b | 7 "" | 8 "" | 9 def mul(a, b): | 10 return a * b
CURRENT = (
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "\n"
    "def sub(a, b):\n"
    "    return a - b\n"
    "\n"
    "\n"
    "def mul(a, b):\n"
    "    return a * b\n"
)

REPLACE_LINE_6 = _diff(
    "--- a/bad.py",
    "+++ b/bad.py",
    "@@ -3,5 +3,5 @@",
    " ",
    " ",
    " def sub(a, b):",
    "-    return a - b",
    "+    return a - b  # fixed",
    " ",
)

FULL_REWRITE = (
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "\n"
    "def sub(a, b):\n"
    "    return a - b  # fixed\n"
    "\n"
    "\n"
    "def mul(a, b):\n"
    "    return a * b\n"
)


def _assert_only_line_changed(before: str, after: str, line_no: int) -> None:
    """The task's acceptance test: exactly one 1-based line differs,
    every other line is byte-identical."""
    b = before.splitlines()
    a = after.splitlines()
    assert len(a) == len(b)
    diffs = [i + 1 for i, (x, y) in enumerate(zip(b, a)) if x != y]
    assert diffs == [line_no], f"changed lines {diffs}, expected only {line_no}"


# ─── diff_hunk.apply_unified_diff ────────────────────────────────────

def test_replace_one_line_only_that_line_changes():
    out = apply_unified_diff(CURRENT, REPLACE_LINE_6)
    assert out is not None
    _assert_only_line_changed(CURRENT, out, 6)
    assert out.splitlines()[5] == "    return a - b  # fixed"


def test_insert_hunk():
    diff = _diff(
        "--- a/bad.py",
        "+++ b/bad.py",
        "@@ -1,3 +1,4 @@",
        " def add(a, b):",
        "     return a + b",
        "+    # sums two numbers",
        " ",
    )
    out = apply_unified_diff(CURRENT, diff)
    assert out is not None
    assert out.splitlines()[:4] == [
        "def add(a, b):", "    return a + b", "    # sums two numbers", "",
    ]
    assert out.splitlines()[4:] == CURRENT.splitlines()[3:]


def test_delete_hunk():
    diff = _diff(
        "--- a/bad.py",
        "+++ b/bad.py",
        "@@ -5,3 +5,2 @@",
        " def sub(a, b):",
        "-    return a - b",
        " ",
    )
    out = apply_unified_diff(CURRENT, diff)
    assert out is not None
    assert len(out.splitlines()) == 9
    assert "return a - b" not in out
    assert out.splitlines()[4] == "def sub(a, b):"
    assert out.splitlines()[5] == ""


def test_two_hunks_in_one_diff():
    diff = _diff(
        "--- a/bad.py",
        "+++ b/bad.py",
        "@@ -1,3 +1,3 @@",
        " def add(a, b):",
        "-    return a + b",
        "+    return a + b  # add",
        " ",
        "@@ -9,2 +9,2 @@",
        " def mul(a, b):",
        "-    return a * b",
        "+    return a * b  # mul",
    )
    out = apply_unified_diff(CURRENT, diff)
    assert out is not None
    assert out.splitlines()[1] == "    return a + b  # add"
    assert out.splitlines()[9] == "    return a * b  # mul"
    diffs = [i + 1 for i, (x, y) in
             enumerate(zip(CURRENT.splitlines(), out.splitlines())) if x != y]
    assert diffs == [2, 10]


def test_context_mismatch_returns_none():
    diff = _diff(
        "--- a/bad.py",
        "+++ b/bad.py",
        "@@ -3,5 +3,5 @@",
        " ",
        " ",
        " def sub(a, b):",
        "-    return a + b",  # wrong: line 6 is "return a - b"
        "+    return a + b  # fixed",
        " ",
    )
    assert apply_unified_diff(CURRENT, diff) is None


def test_malformed_diff_returns_none():
    assert apply_unified_diff(CURRENT, "hello world") is None
    assert apply_unified_diff(CURRENT, "") is None
    assert apply_unified_diff(CURRENT, "   \n") is None
    # Hunk header but no body lines: counts cannot match.
    assert apply_unified_diff(CURRENT, "@@ -1,2 +1,2 @@\n") is None


def test_multi_file_diff_returns_none():
    diff = _diff(
        "--- a/one.py",
        "+++ b/one.py",
        "@@ -1,1 +1,1 @@",
        "-x",
        "+y",
        "--- a/two.py",
        "+++ b/two.py",
        "@@ -1,1 +1,1 @@",
        "-x",
        "+y",
    )
    assert apply_unified_diff(CURRENT, diff) is None


def test_wrong_start_line_unique_anchor_applies():
    # Hunk claims lines 8-10, but the block really sits at lines 5-7.
    diff = _diff(
        "--- a/bad.py",
        "+++ b/bad.py",
        "@@ -8,3 +8,3 @@",
        " def sub(a, b):",
        "-    return a - b",
        "+    return a - b  # fixed",
        " ",
    )
    out = apply_unified_diff(CURRENT, diff)
    assert out is not None
    _assert_only_line_changed(CURRENT, out, 6)


def test_ambiguous_anchor_returns_none():
    dup = (
        "def f():\n"
        "    return 42\n"
        "\n"
        "def g():\n"
        "    return 42\n"
    )
    diff = _diff(
        "--- a/dup.py",
        "+++ b/dup.py",
        "@@ -99,1 +99,1 @@",  # bogus start; block text occurs twice
        "-    return 42",
        "+    return 43",
    )
    assert apply_unified_diff(dup, diff) is None


def test_count_mismatch_returns_none():
    diff = _diff(
        "--- a/bad.py",
        "+++ b/bad.py",
        "@@ -3,5 +3,5 @@",  # header claims 5 old lines; body has 4
        " ",
        " def sub(a, b):",
        "-    return a - b",
        "+    return a - b  # fixed",
        " ",
    )
    assert apply_unified_diff(CURRENT, diff) is None


def test_no_newline_marker():
    # Original has no trailing newline; healed file must not gain one.
    content = "a\nb"
    diff = _diff(
        "--- a/x.txt",
        "+++ b/x.txt",
        "@@ -1,2 +1,2 @@",
        " a",
        "-b",
        "+bb",
        "\\ No newline at end of file",
    )
    out = apply_unified_diff(content, diff)
    assert out == "a\nbb"


def test_pure_insertion_at_file_start():
    diff = _diff(
        "--- a/x.py",
        "+++ b/x.py",
        "@@ -0,0 +1,1 @@",
        "+# header",
    )
    out = apply_unified_diff("def f():\n    pass\n", diff)
    assert out == "# header\ndef f():\n    pass\n"


def test_diff_without_file_headers_applies():
    diff = _diff(
        "@@ -3,5 +3,5 @@",
        " ",
        " ",
        " def sub(a, b):",
        "-    return a - b",
        "+    return a - b  # fixed",
        " ",
    )
    out = apply_unified_diff(CURRENT, diff)
    assert out is not None
    _assert_only_line_changed(CURRENT, out, 6)


# ─── loop_verify.self_heal ───────────────────────────────────────────

class _StubLLM:
    """Records every LLM call; behaviour keyed by max_tokens."""

    def __init__(self, script: dict):
        self.calls: list[int] = []
        self.script = script  # max_tokens -> content | "raise"

    async def __call__(self, *, system, user, max_tokens, mode,
                       user_id=None, review_mode=None, **kw):
        self.calls.append(max_tokens)
        content = self.script.get(max_tokens)
        if content is None:
            content = ""  # unscripted call -> empty reply, not None
        if content == "raise":
            raise RuntimeError("provider down")
        return {"content": content}


async def _heal(llm, errors=None):
    return await loop_verify.self_heal(
        {"path": "bad.py", "content": CURRENT},
        errors if errors is not None else ["bad.py:6:5: E999 SyntaxError"],
        "fix the file",
        user_id="u1",
        call_llm_with_meta=llm,
    )


@pytest.mark.asyncio
async def test_self_heal_surgical_one_line_fix():
    llm = _StubLLM({600: REPLACE_LINE_6})
    out = await _heal(llm)
    assert out is not None
    _assert_only_line_changed(CURRENT, out, 6)
    assert out.splitlines()[5] == "    return a - b  # fixed"
    # Surgical hit: exactly ONE LLM call, the 600-token hunk call.
    assert llm.calls == [600]


@pytest.mark.asyncio
async def test_self_heal_falls_back_when_diff_garbage():
    llm = _StubLLM({600: "not a diff at all", 2500: FULL_REWRITE})
    out = await _heal(llm)
    assert out == FULL_REWRITE.strip()  # fallback path .strip()s content (pre-existing)
    assert llm.calls == [600, 2500]


@pytest.mark.asyncio
async def test_self_heal_falls_back_when_diff_does_not_apply():
    bad_diff = _diff(
        "--- a/bad.py",
        "+++ b/bad.py",
        "@@ -3,5 +3,5 @@",
        " ",
        " ",
        " def sub(a, b):",
        "-    return a + b",  # wrong context: never applies
        "+    return a + b  # fixed",
        " ",
    )
    llm = _StubLLM({600: bad_diff, 2500: FULL_REWRITE})
    out = await _heal(llm)
    assert out == FULL_REWRITE.strip()  # fallback path .strip()s content (pre-existing)
    assert llm.calls == [600, 2500]


@pytest.mark.asyncio
async def test_self_heal_falls_back_on_full_rewrite_decline():
    llm = _StubLLM({600: "FULL-REWRITE", 2500: FULL_REWRITE})
    out = await _heal(llm)
    assert out == FULL_REWRITE.strip()  # fallback path .strip()s content (pre-existing)
    assert llm.calls == [600, 2500]


@pytest.mark.asyncio
async def test_self_heal_skips_surgical_when_many_errors():
    llm = _StubLLM({2500: FULL_REWRITE})
    out = await _heal(llm, errors=[f"bad.py:{i}: E501" for i in range(6)])
    assert out == FULL_REWRITE.strip()  # fallback path .strip()s content (pre-existing)
    # Surgical never attempted: single full-rewrite call only.
    assert llm.calls == [2500]


@pytest.mark.asyncio
async def test_self_heal_returns_none_when_both_fail():
    llm = _StubLLM({600: "garbage", 2500: ""})
    out = await _heal(llm)
    assert out is None
    assert llm.calls == [600, 2500]


@pytest.mark.asyncio
async def test_self_heal_surgical_exception_falls_back():
    llm = _StubLLM({600: "raise", 2500: FULL_REWRITE})
    out = await _heal(llm)
    assert out == FULL_REWRITE.strip()  # fallback path .strip()s content (pre-existing)
    assert llm.calls == [600, 2500]


@pytest.mark.asyncio
async def test_self_heal_rejects_diff_that_empties_file():
    wipe = _diff(
        "--- a/bad.py",
        "+++ b/bad.py",
        "@@ -1,10 +1,0 @@",
        "-def add(a, b):",
        "-    return a + b",
        "-",
        "-",
        "-def sub(a, b):",
        "-    return a - b",
        "-",
        "-",
        "-def mul(a, b):",
        "-    return a * b",
    )
    llm = _StubLLM({600: wipe, 2500: FULL_REWRITE})
    out = await _heal(llm)
    assert out == FULL_REWRITE.strip()  # fallback path .strip()s content (pre-existing)
    assert llm.calls == [600, 2500]
