"""
tests/test_2026_09_13_scope_creep_gate.py

Phase 1 (Architect-Agent ticket, Part 4 broadening) — approved
2026-09-13. Broadens the existing hallucination gate beyond
imports to catch same-file-different-symbol scope creep and
never-shown/never-named file scope creep.

User's explicit adversarial requirement: confirm the broadened gate
does NOT have a blind spot for a model that changes an EXISTING,
correctly-imported file in a way that alters its EXPORTED BEHAVIOR
(not just adding/removing imports) — i.e. same-file-different-symbol
changes. See test_6 below.

No mocks for the deterministic checks (tests 1-8). Test 9 makes a
REAL LLM call, same pattern as the Phase 0 live re-repro.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.cto_projects_helpers import (
    _extract_top_level_symbols,
    _symbol_scope_creep_reasons,
    _file_scope_creep_reasons,
)
from services.cto_pipeline_steps import _run_hallucination_gate

_ORIGINAL = """import React from 'react';
import { formatPrice } from './utils/price';

function formatPrice(amount, currency) {
  return currency + ' ' + Math.round(amount * 100) / 100;
}

function App() {
  return (
    <div>
      <footer className="site-footer">
        <p>&copy; 2025 ReRoots Beauty. All rights reserved.</p>
      </footer>
    </div>
  );
}

export default App;
"""


def test_1_extract_symbols_js_finds_both_functions():
    syms = _extract_top_level_symbols("src/App.js", _ORIGINAL)
    assert "formatPrice" in syms
    assert "App" in syms
    assert "Math.round" in syms["formatPrice"]


def test_2_extract_symbols_python_indentation_balanced():
    py_src = (
        "def price(x):\n"
        "    y = x * 2\n"
        "    return y\n"
        "\n"
        "def other():\n"
        "    return 1\n"
    )
    syms = _extract_top_level_symbols("services/x.py", py_src)
    assert set(syms.keys()) == {"price", "other"}
    assert "y = x * 2" in syms["price"]
    assert "return 1" in syms["other"]


def test_3_faithful_footer_only_edit_never_trips_symbol_gate():
    """The task never mentions `formatPrice` or `App`, but neither
    symbol's body actually changed — must NOT false-positive."""
    good = _ORIGINAL.replace(
        "All rights reserved.", "regression rollback check",
    )
    reasons = _symbol_scope_creep_reasons(
        "update the footer copyright text",
        {"src/App.js": good}, {"src/App.js": _ORIGINAL},
    )
    assert reasons == [], f"false positive on a faithful edit: {reasons}"


def test_4_symbol_body_change_unrelated_to_task_is_flagged():
    """THE USER'S ADVERSARIAL CASE: imports stay intact, the file
    keeps almost all its lines (keep-ratio gate would miss this), but
    an EXISTING correctly-imported function's exported behavior is
    silently altered — not asked for, not mentioned in the task."""
    bad = _ORIGINAL.replace(
        "return currency + ' ' + Math.round(amount * 100) / 100;",
        "return currency + ' ' + Math.round(amount * 100) / 100 * 1.05;"
        "  // sneaky 5% markup",
    ).replace("All rights reserved.", "regression rollback check")
    reasons = _symbol_scope_creep_reasons(
        "update the footer copyright text",
        {"src/App.js": bad}, {"src/App.js": _ORIGINAL},
    )
    assert reasons, "adversarial symbol-body change was NOT flagged"
    assert any("formatPrice" in r for r in reasons)


def test_5_symbol_removed_without_being_asked_is_flagged():
    bad = _ORIGINAL.replace(
        "function formatPrice(amount, currency) {\n"
        "  return currency + ' ' + Math.round(amount * 100) / 100;\n"
        "}\n\n",
        "",
    )
    reasons = _symbol_scope_creep_reasons(
        "update the footer copyright text",
        {"src/App.js": bad}, {"src/App.js": _ORIGINAL},
    )
    assert any("REMOVED" in r and "formatPrice" in r for r in reasons)


def test_6_symbol_change_IS_allowed_when_task_names_it():
    """No false positive when the task explicitly asks to change the
    named symbol — a real, intended edit must not be blocked."""
    bad = _ORIGINAL.replace(
        "return currency + ' ' + Math.round(amount * 100) / 100;",
        "return currency + ' ' + Math.round(amount * 100) / 100 * 1.05;",
    )
    reasons = _symbol_scope_creep_reasons(
        "update formatPrice to add a 5% markup",
        {"src/App.js": bad}, {"src/App.js": _ORIGINAL},
    )
    assert reasons == [], f"false positive when task explicitly named the symbol: {reasons}"


def test_7_file_scope_creep_flags_unnamed_new_file():
    reasons = _file_scope_creep_reasons(
        "update the footer copyright text",
        {"src/App.js": _ORIGINAL, "scripts/random_migration.py": "print(1)"},
        {"src/App.js": _ORIGINAL},
    )
    assert any("random_migration.py" in r for r in reasons)


def test_8_file_scope_creep_allows_task_named_new_file():
    reasons = _file_scope_creep_reasons(
        "create a new file src/utils/newHelper.js that exports noop",
        {"src/App.js": _ORIGINAL, "src/utils/newHelper.js": "export const noop = () => {};"},
        {"src/App.js": _ORIGINAL},
    )
    assert reasons == []


@pytest.mark.asyncio
async def test_9_full_gate_catches_adversarial_symbol_case_and_self_heals_real_call():
    """NO MOCKS — feeds the exact adversarial pattern from test_4
    through the REAL, full `_run_hallucination_gate` (which makes a
    REAL retry LLM call re-injecting the true file). Proves the gate
    both detects AND attempts self-heal end-to-end, same shape as the
    Phase 0 import adversarial test."""
    from routers.cto_projects.worker_api import _AI_SYS

    bad = _ORIGINAL.replace(
        "return currency + ' ' + Math.round(amount * 100) / 100;",
        "return currency + ' ' + Math.round(amount * 100) / 100 * 1.05;"
        "  // sneaky 5% markup",
    ).replace("All rights reserved.", "regression rollback check 2026-09-13")
    contents = {"src/App.js": _ORIGINAL}
    edits = {"src/App.js": bad}
    user_msg = "TASK: update the footer copyright text\n\nFILE: src/App.js"

    final_edits, err = await _run_hallucination_gate(
        task_id="phase1-symbol-adversarial", edits=dict(edits),
        contents=contents, user_msg=user_msg, ai_sys=_AI_SYS,
    )
    if err:
        # Hard-refused to push — acceptable (safe failure mode).
        assert "formatPrice" in err or "task" in err.lower()
        return
    final_body = final_edits.get("src/App.js", "")
    # If it was "fixed" via retry, the unrelated markup must be gone.
    assert "1.05" not in final_body, (
        "gate ALLOWED the unrelated markup change through unflagged"
    )
