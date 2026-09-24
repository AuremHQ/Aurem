"""
tests/test_2026_02_design_linter_console_log_autofix.py

Founder QA finding (2026-02, "console.log:45 is now confirmed as a
REPEAT active blocker, not a one-off"): the SAME pre-existing,
unrelated console.log at frontend/src/index.js:45 blocked TWO
separate, unrelated real commit attempts in one session.

Root cause: `auto_fix_blocks()` is supposed to silently strip safe
console.log statements BEFORE the lint gate can block on them (see
`cto_pipeline_steps.py::_run_lint_gate` — auto-fix runs first, by
design, precisely so a pre-existing console.log doesn't block
unrelated future work). But the OLD `auto_fix_file()` regex
(`^\\s*console\\.log\\s*\\([^)]*\\);\\s*\\n?`, MULTILINE) only matched
a call that was ENTIRELY alone on its line and ended in exactly
`);` — confirmed to silently fail (leave the console.log untouched,
so lint_file's own broader `\\bconsole\\.log\\s*\\(` rule then blocks
the commit) for any of these common real-world shapes:
  - no trailing semicolon: `console.log('x', y)`
  - not alone on the line: `const y = 1; console.log(y);`
  - nested parens in the call: `console.log('a', foo(bar));`
This explains the "repeatable blocker" report precisely: it wasn't
that the SAME broken line couldn't ever be auto-fixed — it's that
whatever the founder's real line 45 actually looks like, it was one
of these unmatched shapes, so it survived every auto-fix pass.
"""
from services.design_linter import auto_fix_file, lint_file


def _still_blocked(fixed_content: str) -> bool:
    return any(i.severity == "block" for i in lint_file("x.js", fixed_content))


def test_no_trailing_semicolon_is_now_stripped():
    src = "console.log('debug', x)\n"
    fixed, fixes = auto_fix_file("x.js", src)
    assert "console.log" not in fixed
    assert fixes
    assert not _still_blocked(fixed)


def test_not_alone_on_line_is_now_stripped_leaving_rest_of_line():
    src = "const y = 1; console.log(y);\n"
    fixed, fixes = auto_fix_file("x.js", src)
    assert "console.log" not in fixed
    assert "const y = 1;" in fixed
    assert not _still_blocked(fixed)


def test_nested_parens_in_call_are_now_stripped():
    src = 'console.log("a", foo(bar));\n'
    fixed, fixes = auto_fix_file("x.js", src)
    assert "console.log" not in fixed
    assert not _still_blocked(fixed)


def test_trailing_comment_after_call_is_preserved():
    src = "  console.log(x); // leftover note\n"
    fixed, fixes = auto_fix_file("x.js", src)
    assert "console.log" not in fixed
    assert "// leftover note" in fixed


def test_multiple_console_logs_in_one_file_all_removed():
    src = (
        "function f() {\n"
        "  console.log('a')\n"
        "  const b = 2; console.log(b);\n"
        "  return b;\n"
        "}\n"
    )
    fixed, fixes = auto_fix_file("x.js", src)
    assert "console.log" not in fixed
    assert "return b;" in fixed
    assert not _still_blocked(fixed)


def test_the_exact_previously_working_shape_still_works_no_regression():
    """The OLD regex's own one working case — alone-on-line, ending in
    `);` — must still work after the rewrite."""
    src = "  console.log(x);\n"
    fixed, fixes = auto_fix_file("x.js", src)
    assert fixed.strip() == ""
    assert fixes


def test_file_with_no_console_log_is_completely_unchanged():
    src = "const x = 1;\nfunction f() { return x + 1; }\n"
    fixed, fixes = auto_fix_file("x.js", src)
    assert fixed == src
    assert fixes == []


def test_transition_all_autofix_still_works_no_regression():
    src = "div { transition: all 0.3s; }\n"
    fixed, fixes = auto_fix_file("x.css", src)
    assert "transition: all" not in fixed
    assert "transition: opacity" in fixed
    assert fixes
