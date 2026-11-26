"""tests/test_prompt3_surgical_diff_2026_11_26.py

PROMPT 3 item 1 (2026-11-26) — surgical-diff fix path in
services/finding_fix_applier.py. The LLM is asked for a unified diff
FIRST (applied via services/diff_hunk.py, PR #377); ANY diff failure
falls back to the existing whole-file rewrite, unchanged.

The tests call _generate_patched_content / _generate_patched_content_multi
directly with a stubbed services.llm.call_llm_with_meta — keyed by
max_tokens, same pattern as test_p1_surgical_heal._StubLLM — so no
real LLM call runs and brain_gateway's drain worker never starts.
The finding is a REAL Vanguard rule (eval_usage / exec_usage) on real
file content, so the re-validation check (_finding_still_present)
is exercised for real, never stubbed.

Token caps (from the module, not magic numbers here):
  single diff 2000 -> whole-file fallback 4000
  multi  diff 3000 -> whole-file fallback 6000
"""
from __future__ import annotations

import pytest

import services.finding_fix_applier as ffa
from services.finding_fix_applier import (
    _SURGICAL_DIFF_MAX_TOKENS,
    _SURGICAL_DIFF_MULTI_MAX_TOKENS,
    _extract_unified_diff,
)

pytestmark = pytest.mark.asyncio

DIFF_MAX = _SURGICAL_DIFF_MAX_TOKENS          # 2000
WHOLE_MAX = 4000
DIFF_MULTI_MAX = _SURGICAL_DIFF_MULTI_MAX_TOKENS  # 3000
WHOLE_MULTI_MAX = 6000

CURRENT = "def run(user_input):\n    return eval(user_input)\n"
FIXED = "def run(user_input):\n    return ast.literal_eval(user_input)\n"

CURRENT_MULTI = (
    "def run(user_input):\n"
    "    return eval(user_input)\n"
    "\n"
    "\n"
    "def run2(cmd):\n"
    "    exec(cmd)\n"
)
FIXED_MULTI = (
    "def run(user_input):\n"
    "    return ast.literal_eval(user_input)\n"
    "\n"
    "\n"
    "def run2(cmd):\n"
    '    print("exec disabled")\n'
)

EVAL_FINDING = {
    "rule_id": "eval_usage", "severity": "CRITICAL", "line": 2,
    "title": "eval usage", "message": "eval() is dangerous",
    "file": "x.py",
}
EXEC_FINDING = {
    "rule_id": "exec_usage", "severity": "CRITICAL", "line": 6,
    "title": "exec usage", "message": "exec() is dangerous",
    "file": "x.py",
}

VALID_DIFF_FENCED = (
    "```diff\n"
    "--- a/x.py\n"
    "+++ b/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def run(user_input):\n"
    "-    return eval(user_input)\n"
    "+    return ast.literal_eval(user_input)\n"
    "```\n"
)

VALID_DIFF_BARE = (
    "--- a/x.py\n"
    "+++ b/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def run(user_input):\n"
    "-    return eval(user_input)\n"
    "+    return ast.literal_eval(user_input)\n"
)

VALID_DIFF_MULTI = (
    "--- a/x.py\n"
    "+++ b/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def run(user_input):\n"
    "-    return eval(user_input)\n"
    "+    return ast.literal_eval(user_input)\n"
    "@@ -5,2 +5,2 @@\n"
    " def run2(cmd):\n"
    "-    exec(cmd)\n"
    '+    print("exec disabled")\n'
)

# Applies cleanly but the eval() call survives — the finding is NOT fixed.
DIFF_APPLIES_BUT_NOT_FIXED = (
    "--- a/x.py\n"
    "+++ b/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def run(user_input):\n"
    "-    return eval(user_input)\n"
    "+    return eval(user_input)  # TODO: fix later\n"
)

# Removed line does not match the file — diff_hunk refuses to apply.
DIFF_CONTEXT_MISMATCH = (
    "--- a/x.py\n"
    "+++ b/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def run(user_input):\n"
    "-    return eval(user_input.strip())\n"
    "+    return ast.literal_eval(user_input)\n"
)

# Applies cleanly, changes a line, fixes NEITHER finding.
DIFF_FIXES_NEITHER = (
    "--- a/x.py\n"
    "+++ b/x.py\n"
    "@@ -1,1 +1,2 @@\n"
    " def run(user_input):\n"
    "+# note\n"
)

# Fixes eval only — exec survives. Multi path accepts partial fixes.
DIFF_FIXES_EVAL_ONLY = (
    "--- a/x.py\n"
    "+++ b/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def run(user_input):\n"
    "-    return eval(user_input)\n"
    "+    return ast.literal_eval(user_input)\n"
)


class _StubLLM:
    """Records every LLM call; behaviour keyed by max_tokens."""

    def __init__(self, script: dict):
        self.calls: list[tuple] = []  # (max_tokens, system, user)
        self.script = script  # max_tokens -> content | "raise"

    async def __call__(self, *, system, user, max_tokens, mode,
                       user_id=None, review_mode=None, **kw):
        self.calls.append((max_tokens, system, user))
        content = self.script.get(max_tokens)
        if content is None:
            content = ""  # unscripted call -> empty reply, not None
        if content == "raise":
            raise RuntimeError("provider down")
        return {"content": content}


def _tokens(llm: _StubLLM) -> list[int]:
    return [c[0] for c in llm.calls]


def _install(monkeypatch, script: dict) -> _StubLLM:
    llm = _StubLLM(script)
    monkeypatch.setattr("services.llm.call_llm_with_meta", llm)
    return llm


async def _gen_single(*, content: str, finding: dict = EVAL_FINDING):
    return await ffa._generate_patched_content(
        path="x.py", current_content=content, finding=finding,
        user_id="u1", db=None,
    )


async def _gen_multi(*, content: str, findings: list[dict]):
    return await ffa._generate_patched_content_multi(
        path="x.py", current_content=content, findings=findings,
        user_id="u1", db=None,
    )


# ─── (a) valid diff → diff path, whole-file path NOT invoked ─────────

async def test_single_valid_fenced_diff_patches_via_diff_path(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MAX: VALID_DIFF_FENCED})
    patched, err = await _gen_single(content=CURRENT)
    assert err is None
    assert patched == FIXED
    # Surgical hit: exactly ONE LLM call, the diff call. The
    # whole-file rewrite (4000) was never attempted.
    assert _tokens(llm) == [DIFF_MAX]
    system, user = llm.calls[0][1], llm.calls[0][2]
    assert "UNIFIED DIFF" in system
    assert "COMPLETE new file content" not in user


async def test_single_valid_bare_diff_patches_via_diff_path(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MAX: VALID_DIFF_BARE})
    patched, err = await _gen_single(content=CURRENT)
    assert err is None
    assert patched == FIXED
    assert _tokens(llm) == [DIFF_MAX]


async def test_multi_valid_two_hunk_diff_patches_via_diff_path(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MULTI_MAX: VALID_DIFF_MULTI})
    patched, err = await _gen_multi(
        content=CURRENT_MULTI, findings=[EVAL_FINDING, EXEC_FINDING])
    assert err is None
    assert patched == FIXED_MULTI
    assert _tokens(llm) == [DIFF_MULTI_MAX]


# ─── (b) garbage diff → whole-file fallback still fixes ──────────────

async def test_single_garbage_diff_falls_back_to_whole_file(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MAX: "not a diff at all",
                                 WHOLE_MAX: FIXED})
    patched, err = await _gen_single(content=CURRENT)
    assert err is None
    assert patched == FIXED.strip()  # whole-file path .strip()s (pre-existing)
    assert _tokens(llm) == [DIFF_MAX, WHOLE_MAX]
    # The fallback call is the ORIGINAL whole-file prompt, unchanged:
    # same shared body, ends with the complete-content instruction.
    assert llm.calls[1][2].endswith(
        "Return the COMPLETE new file content with the finding fixed. "
        "No fences. No commentary. Just the bytes that should overwrite "
        "the file.")
    assert llm.calls[1][2].startswith(
        llm.calls[0][2].split("Return ONLY a unified diff")[0])


async def test_single_whole_file_echo_to_diff_prompt_falls_back(monkeypatch):
    # The model ignores the diff instruction and returns the full
    # file instead — no @@ hunk, so the diff path declines and the
    # whole-file rewrite runs.
    llm = _install(monkeypatch, {DIFF_MAX: FIXED, WHOLE_MAX: FIXED})
    patched, err = await _gen_single(content=CURRENT)
    assert err is None
    assert patched == FIXED.strip()  # whole-file path .strip()s (pre-existing)
    assert _tokens(llm) == [DIFF_MAX, WHOLE_MAX]


async def test_single_diff_llm_exception_falls_back(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MAX: "raise", WHOLE_MAX: FIXED})
    patched, err = await _gen_single(content=CURRENT)
    assert err is None
    assert patched == FIXED.strip()  # whole-file path .strip()s (pre-existing)
    assert _tokens(llm) == [DIFF_MAX, WHOLE_MAX]


async def test_single_full_rewrite_decline_falls_back(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MAX: "FULL-REWRITE",
                                 WHOLE_MAX: FIXED})
    patched, err = await _gen_single(content=CURRENT)
    assert err is None
    assert patched == FIXED.strip()  # whole-file path .strip()s (pre-existing)
    assert _tokens(llm) == [DIFF_MAX, WHOLE_MAX]


async def test_multi_garbage_diff_falls_back_to_whole_file(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MULTI_MAX: "garbage",
                                 WHOLE_MULTI_MAX: FIXED_MULTI})
    patched, err = await _gen_multi(
        content=CURRENT_MULTI, findings=[EVAL_FINDING, EXEC_FINDING])
    assert err is None
    assert patched == FIXED_MULTI.strip()  # whole-file path .strip()s (pre-existing)
    assert _tokens(llm) == [DIFF_MULTI_MAX, WHOLE_MULTI_MAX]


# ─── (c) diff applies but does NOT fix → fallback ─────────────────────

async def test_single_diff_applies_but_not_fixed_falls_back(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MAX: DIFF_APPLIES_BUT_NOT_FIXED,
                                 WHOLE_MAX: FIXED})
    patched, err = await _gen_single(content=CURRENT)
    assert err is None
    assert patched == FIXED.strip()  # whole-file path .strip()s (pre-existing)
    # The diff applied cleanly — but eval_usage still fires, so the
    # re-validation rejects it and the whole-file rewrite runs.
    assert _tokens(llm) == [DIFF_MAX, WHOLE_MAX]


async def test_multi_diff_fixes_neither_falls_back(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MULTI_MAX: DIFF_FIXES_NEITHER,
                                 WHOLE_MULTI_MAX: FIXED_MULTI})
    patched, err = await _gen_multi(
        content=CURRENT_MULTI, findings=[EVAL_FINDING, EXEC_FINDING])
    assert err is None
    assert patched == FIXED_MULTI.strip()  # whole-file path .strip()s (pre-existing)
    assert _tokens(llm) == [DIFF_MULTI_MAX, WHOLE_MULTI_MAX]


async def test_multi_diff_fixing_one_of_two_is_accepted(monkeypatch):
    # Multi-path semantics: a patch that resolves >=1 finding is a
    # partial fix (the caller classifies the rest as remaining) —
    # the diff path accepts it, no whole-file call happens.
    llm = _install(monkeypatch, {DIFF_MULTI_MAX: DIFF_FIXES_EVAL_ONLY})
    patched, err = await _gen_multi(
        content=CURRENT_MULTI, findings=[EVAL_FINDING, EXEC_FINDING])
    assert err is None
    assert "ast.literal_eval(user_input)" in patched
    assert "exec(cmd)" in patched  # the other finding survives
    assert _tokens(llm) == [DIFF_MULTI_MAX]


# ─── (d) diff_hunk edge cases through the applier path ───────────────

async def test_single_context_mismatch_falls_back(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MAX: DIFF_CONTEXT_MISMATCH,
                                 WHOLE_MAX: FIXED})
    patched, err = await _gen_single(content=CURRENT)
    assert err is None
    assert patched == FIXED.strip()  # whole-file path .strip()s (pre-existing)
    assert _tokens(llm) == [DIFF_MAX, WHOLE_MAX]


async def test_single_truncated_diff_falls_back(monkeypatch):
    # Hunk header claims 2 old lines but the body carries 1 — the
    # count check in diff_hunk refuses before anything is applied.
    truncated = (
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def run(user_input):\n"
        "-    return eval(user_input)\n"
    )
    llm = _install(monkeypatch, {DIFF_MAX: truncated, WHOLE_MAX: FIXED})
    patched, err = await _gen_single(content=CURRENT)
    assert err is None
    assert patched == FIXED.strip()  # whole-file path .strip()s (pre-existing)
    assert _tokens(llm) == [DIFF_MAX, WHOLE_MAX]


async def test_single_empty_content_skips_diff_path(monkeypatch):
    # Missing-file-rule creation (content == "") has nothing to diff
    # against — the whole-file path runs directly, once.
    llm = _install(monkeypatch, {WHOLE_MAX: FIXED})
    patched, err = await _gen_single(content="")
    assert err is None
    assert patched == FIXED.strip()  # whole-file path .strip()s (pre-existing)
    assert _tokens(llm) == [WHOLE_MAX]


async def test_both_paths_fail_returns_whole_file_error(monkeypatch):
    llm = _install(monkeypatch, {DIFF_MAX: "garbage", WHOLE_MAX: ""})
    patched, err = await _gen_single(content=CURRENT)
    assert patched == ""
    assert err == "llm_empty_response"
    assert _tokens(llm) == [DIFF_MAX, WHOLE_MAX]


# ─── _extract_unified_diff unit checks ───────────────────────────────

def test_extract_unified_diff_variants():
    assert _extract_unified_diff(VALID_DIFF_FENCED) is not None
    assert "@@" in _extract_unified_diff(VALID_DIFF_FENCED)
    assert _extract_unified_diff(VALID_DIFF_BARE) == VALID_DIFF_BARE.strip()
    # No hunk at all — whole file, garbage, empty: all decline.
    assert _extract_unified_diff("not a diff at all") is None
    assert _extract_unified_diff(FIXED) is None
    assert _extract_unified_diff("") is None
    assert _extract_unified_diff("```python\n" + FIXED + "```\n") is None
    # Prose before a bare diff is skipped, fence kept out.
    assert _extract_unified_diff(
        "Here is the fix:\n" + VALID_DIFF_BARE) == VALID_DIFF_BARE.strip()
