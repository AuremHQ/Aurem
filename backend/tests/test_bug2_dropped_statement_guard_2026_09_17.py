"""
tests/test_bug2_dropped_statement_guard_2026_09_17.py

Bug 2 fix — live incident, commit 36ecdee on TJSNDHU/Aurem. Verified
against the real commit via the GitHub API: the task asked ONLY to
"add a regression test comment" to `frontend/src/index.js`. The
import line was untouched (contrary to the bug report's exact
wording), but TWO real, unrelated `console.log(...)` statements
silently vanished — one a plain module-level log, one inside an
existing function. Neither the import-drop gate nor the symbol-
scope-creep gate catches a bare statement disappearing, so this adds
`_dropped_statement_reasons()` and wires it into the hallucination
gate.

No mocks for the deterministic checks — same convention as
test_2026_09_13_scope_creep_gate.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.cto_projects_helpers import _dropped_statement_reasons

# Simplified reproduction of the real frontend/src/index.js shape.
_ORIGINAL = """import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

console.log('[AUREM] Autonomous AI Workforce Platform Starting...');

(function healUrl() {
  if (process.env.NODE_ENV === 'development') {
    console.log('[AUREM] API URL auto-healer installed (development mode)');
  }
})();

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
"""


def test_add_comment_task_that_drops_a_console_log_is_flagged():
    """Reproduces the exact incident: task says "add a comment",
    model also silently deletes the startup console.log."""
    new_content = """import React from 'react';
// regression test round3
import ReactDOM from 'react-dom/client';
import App from './App';

(function healUrl() {
  if (process.env.NODE_ENV === 'development') { }
})();

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
"""
    reasons = _dropped_statement_reasons(
        "add a regression test comment to this file",
        {"frontend/src/index.js": new_content},
        {"frontend/src/index.js": _ORIGINAL},
    )
    assert reasons, "must flag the silently-dropped console.log statements"
    assert "console.log" in reasons[0]


def test_add_comment_task_that_only_adds_is_not_flagged():
    """A genuinely faithful "add a comment" edit — nothing removed —
    must produce zero reasons (no false positive)."""
    new_content = _ORIGINAL.replace(
        "console.log('[AUREM] Autonomous AI Workforce Platform Starting...');",
        "// regression test round3\n"
        "console.log('[AUREM] Autonomous AI Workforce Platform Starting...');",
    )
    reasons = _dropped_statement_reasons(
        "add a regression test comment to this file",
        {"frontend/src/index.js": new_content},
        {"frontend/src/index.js": _ORIGINAL},
    )
    assert reasons == []


def test_legitimate_refactor_task_is_never_flagged():
    """A real, substantial task (not an "add a marker"-shaped one)
    that removes a call expression as an intended part of the fix
    must NOT be flagged — this check only activates on trivial
    additive tasks, by design, to avoid noise on real work."""
    new_content = _ORIGINAL.replace(
        "console.log('[AUREM] Autonomous AI Workforce Platform Starting...');\n\n",
        "",
    )
    reasons = _dropped_statement_reasons(
        "remove the noisy startup logging from index.js",
        {"frontend/src/index.js": new_content},
        {"frontend/src/index.js": _ORIGINAL},
    )
    assert reasons == [], "explicit removal intent must be allowed through"


def test_unrelated_task_is_never_flagged():
    """A task with no "add a comment/marker" framing at all must not
    trigger this check even if it happens to also drop a line — the
    general-purpose scope-creep/import gates already cover that."""
    new_content = _ORIGINAL.replace(
        "console.log('[AUREM] Autonomous AI Workforce Platform Starting...');\n\n",
        "",
    )
    reasons = _dropped_statement_reasons(
        "fix the footer copyright year",
        {"frontend/src/index.js": new_content},
        {"frontend/src/index.js": _ORIGINAL},
    )
    assert reasons == []


def test_brand_new_file_is_never_flagged():
    reasons = _dropped_statement_reasons(
        "add a comment to this new file",
        {"frontend/new_file.js": "// regression test round3\nexport default 1;\n"},
        {},
    )
    assert reasons == []
