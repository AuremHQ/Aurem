"""
tests/test_handoff_diff_safety_guard_2026_02.py

Regression lock for a live-repro'd CRITICAL bug this same engagement
(founder QA report, TJSNDHU/Aurem): a trivial "add a comment" request
came back with a diff on frontend/src/index.js showing "+2 / -47",
dropping all 4 top-level imports (React, ReactDOM, index.css, App) —
marked "plan clean ✓" with an active "Approve the fix" button. This
is the SAME class of bug as the earlier "App.js +2/-58" incident
(2026-09-13), but a DIFFERENT, ungated code path: that fix
(`_dropped_import_reasons()` etc. in cto_projects_helpers.py) only
runs inside worker_api.py's task-execution pipeline, AFTER a user
clicks "Approve the fix". The diff/badge the user actually SAW comes
from the frontend's client-side diff-peek feature
(`extractShipFiles`/`FileDiffPeek` in MessageBubble.jsx), which reads
whatever fenced code block the model happens to write ALONGSIDE its
```aurem-handoff brief (a violation of that fence's own documented
contract — "No code blocks inside — the worker writes the code
itself based on this brief") and renders a live diff with ZERO
verification, before Approve is ever clicked.

Fix (services/commit_boundary.py): a well-formed handoff reply must
never contain any OTHER fenced code block. Strip every fenced block
that isn't the handoff fence itself, so `extractShipFiles` finds
nothing to diff-peek and the misleading, ungated preview simply never
renders — a deterministic contract enforcement, not an LLM-trusted
instruction.
"""
from services.commit_boundary import strip_stray_code_blocks_near_handoff


def test_stray_code_block_before_handoff_fence_is_stripped():
    # Live repro shape (index.js, dropped imports).
    leaked = (
        "I added the comment to frontend/src/index.js.\n\n"
        "```js\n"
        "// qa regression test\n"
        "const root = ReactDOM.createRoot(document.getElementById(\"root\"));\n"
        "root.render(<App />);\n"
        "```\n\n"
        "```aurem-handoff\n"
        "In frontend/src/index.js add the comment "
        "// qa regression test at the top of the file, above the "
        "existing imports.\n"
        "```\n"
    )
    out = strip_stray_code_blocks_near_handoff(leaked)
    assert "```js" not in out
    assert "ReactDOM.createRoot" not in out
    # The handoff fence itself, and its brief text, survive intact.
    assert "```aurem-handoff" in out
    assert "add the comment" in out


def test_stray_code_block_after_handoff_fence_is_stripped():
    leaked = (
        "```aurem-handoff\n"
        "Fix the footer text in Footer.jsx.\n"
        "```\n\n"
        "Here is an example:\n"
        "```jsx\n"
        "export default function Footer() { return <div>hi</div>; }\n"
        "```\n"
    )
    out = strip_stray_code_blocks_near_handoff(leaked)
    assert "```jsx" not in out
    assert "export default function Footer" not in out
    assert "```aurem-handoff" in out
    assert "Fix the footer text" in out


def test_stray_code_blocks_on_both_sides_are_stripped():
    leaked = (
        "```js\nconst a = 1;\n```\n"
        "some text\n"
        "```aurem-handoff\nAdd logging to main.py.\n```\n"
        "```py\nx = 1\n```\n"
    )
    out = strip_stray_code_blocks_near_handoff(leaked)
    assert "const a = 1" not in out
    assert "x = 1" not in out
    assert "```aurem-handoff" in out
    assert "Add logging to main.py" in out


def test_well_formed_handoff_with_no_stray_blocks_is_untouched():
    clean = "```aurem-handoff\nFix the footer text.\n```"
    assert strip_stray_code_blocks_near_handoff(clean) == clean


def test_no_handoff_fence_is_a_true_noop():
    # Must never touch a normal chat reply that legitimately shows
    # code with no ship flow attached.
    normal = "Here is some code:\n```js\nconsole.log(1);\n```\n"
    assert strip_stray_code_blocks_near_handoff(normal) == normal
    assert strip_stray_code_blocks_near_handoff("") == ""
    assert strip_stray_code_blocks_near_handoff(None) is None


def test_wired_into_apply_output_guards():
    from services.chat_helpers import apply_output_guards
    leaked = (
        "I added the comment.\n\n"
        "```js\nconst root = ReactDOM.createRoot(x);\n```\n\n"
        "```aurem-handoff\n"
        "In frontend/src/index.js add the comment at the top of the file.\n"
        "```\n"
    )
    out = apply_output_guards("add a comment", leaked, False, "", tool_calls_run=1)
    assert "```js" not in out
    assert "ReactDOM.createRoot" not in out
    assert "```aurem-handoff" in out
