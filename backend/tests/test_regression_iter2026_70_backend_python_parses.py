"""tests/test_regression_iter2026_70_backend_python_parses.py

Regression: seven backend modules on main were corrupted by automated
edit/merge output (truncated to a fragment, or with model chatter
pasted into the source) and no longer parse:
  routers/github_app.py (imported by main.py at top level, so the
  whole backend failed to start), routers/admin_analytics.py (3031 ->
  278 lines), routers/admin_gateway.py, routers/admin_bin.py,
  services/generation_rules_triggers.py, scripts/g21_security_scan.py,
  cto_services/codebase_indexer.py.

This test compiles every .py file under backend/ so a corrupted file
fails CI at the source instead of at the next deploy.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", "node_modules", ".venv", "venv", ".git"}

# Modules main.py imports at module level: if one of these does not
# parse, the API server cannot start.
STARTUP_CRITICAL = [
    "routers/github_app.py", "routers/admin_analytics.py",
    "routers/admin_gateway.py", "routers/admin_bin.py",
    "services/local_tools.py", "main.py",
]


def _py_files():
    for p in sorted(BACKEND.rglob("*.py")):
        if SKIP_DIRS.isdisjoint(p.relative_to(BACKEND).parts):
            yield p


def _label(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(BACKEND))
    except ValueError:
        return str(path)


def _parse_error(path: pathlib.Path):
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        return f"{_label(path)}:{e.lineno}: {e.msg}"
    return None


def test_every_backend_python_file_parses():
    errors = [e for e in map(_parse_error, _py_files()) if e]
    assert not errors, "unparseable backend files:\n" + "\n".join(errors)


@pytest.mark.parametrize("rel", STARTUP_CRITICAL)
def test_startup_critical_modules_parse(rel):
    path = BACKEND / rel
    assert path.exists(), rel
    assert _parse_error(path) is None


def test_corruption_is_detected_by_the_checker(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("x = foo(\n", encoding="utf-8")
    assert _parse_error(bad) is not None


@pytest.mark.parametrize("rel,min_lines", [
    ("routers/github_app.py", 1000),
    ("routers/admin_analytics.py", 2500),
    ("routers/admin_bin.py", 1000),
])
def test_big_routers_are_not_truncated_to_a_fragment(rel, min_lines):
    n = len((BACKEND / rel).read_text(encoding="utf-8").splitlines())
    assert n >= min_lines, f"{rel} has only {n} lines"


def test_no_model_chatter_pasted_into_source():
    needles = ("Wait — I must", "I'll rewrite it as", "Final decision:",
               "rest of the file remains exactly the same")
    hits = []
    for p in _py_files():
        if p.name == pathlib.Path(__file__).name:
            continue
        text = p.read_text(encoding="utf-8")
        for n in needles:
            if n in text:
                hits.append(f"{p.relative_to(BACKEND)}: {n}")
    assert not hits, "\n".join(hits)
