"""tests/test_regression_iter2026_10t_architect_dead_code_wallet_guard.py

Closing-the-bypass-doors audit — Step 4.

`services/architect_agent.py::invoke_architect` makes a real `call_llm`
call but has NO caller anywhere in `routers/`/`services/` outside
tests today — `routers/architect.py` only exposes a read-only
blueprint GET. Per the master prompt: don't delete it (it may be
wired up later), but make it impossible to wire up WITHOUT a budget
gate by accident.

Same "static source-contract" pattern already established by
test_iter212m46_mode_d_no_autoship.py (which asserts Mode D's
fix-confirm path never calls `_enqueue_cto_task`) — this is a real
architectural invariant, not a stand-in for a behavioral test: there
is nothing to execute (the function has no live caller to exercise),
so the invariant worth protecting is "nobody wires a caller that
skips the required gate."
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_invoke_architect_docstring_documents_the_wallet_requirement():
    from services.architect_agent import invoke_architect
    doc = inspect.getdoc(invoke_architect) or ""
    assert "assert_wallet" in doc, (
        "invoke_architect's docstring must keep telling a future caller "
        "it needs a wallet/entitlement gate BEFORE calling it — there's "
        "no gate inside the function itself (no HTTP context to 402 from)."
    )


def test_no_router_or_service_calls_invoke_architect_without_a_wallet_gate():
    """Real source-walk (not a plain substring grep): finds every
    function body anywhere in `routers/` or `services/` that calls
    `invoke_architect`, and asserts that SAME function also references
    `assert_wallet` or `assert_fix_entitled` somewhere in its own body.
    Passes vacuously today (zero real callers) and starts actually
    enforcing the invariant the moment someone adds one."""
    violations = []
    for base in ("routers", "services"):
        for path in (BACKEND_DIR / base).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                src = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "invoke_architect" not in src or path.name == "architect_agent.py":
                continue
            try:
                tree = ast.parse(src, filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                fn_src = ast.get_source_segment(src, node) or ""
                if "invoke_architect" not in fn_src:
                    continue
                if "assert_wallet" not in fn_src and "assert_fix_entitled" not in fn_src:
                    violations.append(f"{path.relative_to(BACKEND_DIR)}::{node.name}")
    assert violations == [], (
        "a caller of invoke_architect was added without a wallet/"
        f"entitlement gate in the same function: {violations}"
    )
