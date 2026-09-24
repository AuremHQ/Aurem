"""
Bug 5 fix (2026-09-17) — "Parallel codegen (1 agents)" instead of
"1 agent". Naive `f"{n} {word}s"` string concatenation with no
singular handling.
"""
from services.plural_utils import pluralize


def test_pluralize_singular_and_plural():
    assert pluralize(1, "agent") == "1 agent"
    assert pluralize(2, "agent") == "2 agents"
    assert pluralize(0, "agent") == "0 agents"


def test_pluralize_irregular_form():
    assert pluralize(1, "child", "children") == "1 child"
    assert pluralize(4, "child", "children") == "4 children"


def test_full_scan_orchestrator_uses_singular_for_one_file():
    from services.full_scan_orchestrator import should_run_full_scan
    files = [{"path": "app.py", "content": "print('hi')\n"}]
    _ok, reason = should_run_full_scan(files)
    assert "1 file," in reason
    assert "1 files" not in reason
