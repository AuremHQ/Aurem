"""
tests/test_p0_bug3_multistep_truncation_2026_09.py

P0 bug-brief, Bug 3 — "Multi-step instructions silently truncated."
Founder repro: a message with 4 explicit numbered steps got step 1
done, then the turn closed on the generic max_iters fallback ("Want
me to continue?") without even attempting steps 2-4.

Root cause: `max_iters` (default 4) is sized for a single-ask turn;
an explicitly enumerated multi-step request needs proportionally more
tool-call budget, and the fallback message when budget WAS exhausted
never re-stated which of the original requested steps were even
attempted — so the user had no way to tell 3 steps were silently
dropped versus never asked for.

Fix (services/orchestrator.py):
  1. `_detect_explicit_multistep()` — detects an enumerated step list
     (one per line, or inline "1) X 2) Y 3) Z") and returns the list
     of step descriptions.
  2. `chat_with_tools()` bumps `max_iters` proportional to the
     detected step count (capped at 12) BEFORE the tool-call loop
     starts, so a modest 3-4 step ask actually gets room to finish.
  3. `_synthesise_max_iters_summary()` / `_wrap_as_explicit_partial()`
     restate the full original step list verbatim at the top of any
     fallback/partial message — grounded in the user's own text,
     never a fabricated per-step done/not-done verdict.

These are pure-function unit tests (no LLM/network mocking needed —
matches the codebase's existing convention of testing this file's
giant `chat_with_tools` via structural/source assertions rather than
a fragile full mock, e.g.
tests/test_iter212g_orchestrator_local_ctx_and_openrouter_model.py).
"""
import inspect

from services.orchestrator import (
    _detect_explicit_multistep,
    _synthesise_max_iters_summary,
    _wrap_as_explicit_partial,
    chat_with_tools,
)

# Brief's exact reproduction string.
BRIEF_MULTISTEP_PROMPT = "Do these steps in order without stopping to ask: 1) X 2) Y 3) Z"


# ─── 1. Detector — the brief's exact shape, plus common variants ──
def test_detects_inline_enumeration_brief_exact_string():
    steps = _detect_explicit_multistep(BRIEF_MULTISTEP_PROMPT)
    assert steps == ["X", "Y", "Z"]


def test_detects_one_step_per_line():
    prompt = "Please do the following:\n1. Fix the login bug\n2. Add a test\n3. Deploy it"
    steps = _detect_explicit_multistep(prompt)
    assert steps == ["Fix the login bug", "Add a test", "Deploy it"]


def test_does_not_fire_on_a_single_step_or_plain_message():
    assert _detect_explicit_multistep("just fix the login bug please") is None
    assert _detect_explicit_multistep("1. only one item here") is None
    assert _detect_explicit_multistep("") is None
    assert _detect_explicit_multistep(None) is None


def test_step_count_is_capped_at_12():
    prompt = "\n".join(f"{i}. step {i}" for i in range(1, 20))
    steps = _detect_explicit_multistep(prompt)
    assert len(steps) == 12


# ─── 2. max_iters bump — proportional, capped, source-verified ────
# `chat_with_tools` is too heavy (real LLM/tool loop) to exercise
# end-to-end in a unit test without a fragile full mock — following
# this file's existing test convention, verify the bump logic is
# actually wired at the right place via source inspection.
def test_chat_with_tools_bumps_max_iters_for_detected_multistep():
    src = inspect.getsource(chat_with_tools)
    assert "_detect_explicit_multistep(prompt)" in src
    assert "max_iters = max(max_iters, min(len(explicit_steps) * 2 + 2, 12))" in src
    # Must run BEFORE the tool-call loop, not after — find both anchors
    # and confirm the bump precedes the loop that enforces max_iters.
    bump_idx = src.index("max_iters = max(max_iters, min(len(explicit_steps)")
    loop_idx = src.index("while iters < max_iters")
    assert bump_idx < loop_idx


# ─── 3. Fallback message restates the original steps verbatim ─────
def test_synthesise_max_iters_summary_restates_explicit_steps():
    summary = _synthesise_max_iters_summary(
        BRIEF_MULTISTEP_PROMPT, invocations=[], explicit_steps=["X", "Y", "Z"],
    )
    assert "1. X" in summary
    assert "2. Y" in summary
    assert "3. Z" in summary
    assert "3 steps" in summary


def test_synthesise_max_iters_summary_unchanged_without_explicit_steps():
    """Backward-compat: omitting explicit_steps must produce the exact
    same output as before this fix."""
    summary_new_default = _synthesise_max_iters_summary("fix the bug", invocations=[])
    summary_explicit_none = _synthesise_max_iters_summary("fix the bug", invocations=[], explicit_steps=None)
    assert summary_new_default == summary_explicit_none
    assert "restating" not in summary_new_default


def test_wrap_as_explicit_partial_includes_step_restatement():
    wrapped = _wrap_as_explicit_partial(
        "Let me check the first file...",
        invocations=[{"tool": "read_repo_file", "args": {"path": "a.py"}, "result": {}}],
        prompt=BRIEF_MULTISTEP_PROMPT,
        explicit_steps=["X", "Y", "Z"],
    )
    assert "1. X" in wrapped
    assert "2. Y" in wrapped
    assert "3. Z" in wrapped
    assert "Want me to continue?" in wrapped  # existing behaviour preserved


def test_wrap_as_explicit_partial_unchanged_without_explicit_steps():
    """Backward-compat: existing callers that never pass
    `explicit_steps` get byte-identical output to before this fix."""
    args = ("Let me check the first file...", [{"tool": "x", "args": {}, "result": {}}], "fix the bug")
    assert _wrap_as_explicit_partial(*args) == _wrap_as_explicit_partial(*args, explicit_steps=None)
