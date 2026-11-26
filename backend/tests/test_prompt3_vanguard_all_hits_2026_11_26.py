"""
Prompt 3 (2026-11-26) — _scan_round2_file must report ALL hits per rule
per file, not just the first.

Regression tests for the removal of the one-hit-per-rule `break` in
`backend/services/vanguard_scanner.py::_scan_round2_file` (was L578:
`break  # one hit per rule per file is enough for the report`).

Before the fix a file with 12 SQL injections reported 1
`sql_string_format_deep` hit; after the fix it reports all 12.
No cap was added: per-hit cost is one dict + one <=21-line context
slice, patterns are pre-compiled, and the per-line deadline check
still bounds pathological files — so there is no truncation case
to test (no cap, no truncation flag).

These are PURE unit tests — no HTTP, no GitHub, no LLM.
"""
from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.vanguard_scanner import (
    _scan_round2_file,
    run_two_round_scan,
)


def _deadline(seconds: float = 30.0) -> float:
    return time.monotonic() + seconds


def _sql_lines(n: int) -> str:
    """`n` distinct lines, each a separate f-string SQL injection."""
    return "\n".join(
        f'cursor.execute(f"SELECT * FROM users WHERE id={{user_id_{i}}}")'
        for i in range(n)
    )


def test_round2_reports_all_hits_of_one_rule():
    """3 SQL injections in one file -> 3 sql_string_format_deep hits
    (pre-fix: 1). Lines and context must be per-hit, not all pointing
    at the first line."""
    text = _sql_lines(3)
    hits = _scan_round2_file("src/db.py", text, deadline=_deadline())
    sql_hits = [h for h in hits if h["name"] == "sql_string_format_deep"]
    assert len(sql_hits) == 3, f"expected 3 hits, got {len(sql_hits)}: {hits}"
    assert sorted(h["line"] for h in sql_hits) == [1, 2, 3]
    for h in sql_hits:
        assert h["context_lines"], "context_lines missing from R2 hit"
        assert h["snippet"].startswith("cursor.execute")


def test_round2_reports_twelve_sql_injections():
    """The audit's example: a file with 12 SQL injections reports 12,
    not 1."""
    hits = _scan_round2_file("src/db.py", _sql_lines(12), deadline=_deadline())
    sql_hits = [h for h in hits if h["name"] == "sql_string_format_deep"]
    assert len(sql_hits) == 12
    assert sorted(h["line"] for h in sql_hits) == list(range(1, 13))


def test_round2_reports_all_hits_for_multiple_rules():
    """Two rules in one file each report all their hits: 2 SQL
    injections + 2 hardcoded AWS keys + 2 OpenAI keys."""
    text = "\n".join([
        'cursor.execute(f"SELECT * FROM u WHERE id={uid_a}")',
        "AWS_KEY_1 = \"AKIAIOSFODNN7EXAMPLE\"",
        "OPENAI_KEY_1 = \"sk-" + "a" * 32 + "\"",
        'cursor.execute(f"SELECT * FROM u WHERE id={uid_b}")',
        "AWS_KEY_2 = \"AKIAZZZZZZZZZZZZZZZZ\"",
        "OPENAI_KEY_2 = \"sk-" + "b" * 32 + "\"",
    ])
    hits = _scan_round2_file("src/mixed.py", text, deadline=_deadline())
    by_rule: dict[str, list[dict]] = {}
    for h in hits:
        by_rule.setdefault(h["name"], []).append(h)
    assert len(by_rule.get("sql_string_format_deep", [])) == 2
    assert len(by_rule.get("secret_aws_access_key_deep", [])) == 2
    assert len(by_rule.get("secret_openai_key_deep", [])) == 2
    # Each hit keeps its own line number.
    assert sorted(h["line"] for h in by_rule["secret_aws_access_key_deep"]) == [2, 5]


def test_round2_reports_zero_hits_for_clean_file():
    """A file with no deep-pattern matches still reports zero —
    removing the break must not invent findings."""
    hits = _scan_round2_file(
        "src/clean.py", "print('hello world')\n", deadline=_deadline()
    )
    assert hits == []


def test_round2_still_skips_ignored_lines_but_reports_other_hits():
    """A `vanguard: ignore` line is still suppressed, while the other
    hits of the same rule in the same file are still reported (the
    loop must keep scanning past a suppressed line, not stop)."""
    text = (
        'cursor.execute(f"SELECT * FROM u WHERE id={uid_a}")  # vanguard: ignore\n'
        'cursor.execute(f"SELECT * FROM u WHERE id={uid_b}")\n'
        'cursor.execute(f"SELECT * FROM u WHERE id={uid_c}")'
    )
    hits = _scan_round2_file("src/db.py", text, deadline=_deadline())
    sql_hits = [h for h in hits if h["name"] == "sql_string_format_deep"]
    assert sorted(h["line"] for h in sql_hits) == [2, 3]


def test_two_round_scan_surfaces_all_hits_end_to_end():
    """End to end via run_two_round_scan: the 3 SQL injections reach
    round2_findings AND the deduplicated combined list (Round 1 flags
    the file via sql_string_format, Round 2 then reports all 3)."""
    res = run_two_round_scan({"src/db.py": _sql_lines(3)})
    r2_sql = [f for f in res["round2_findings"]
              if f["name"] == "sql_string_format_deep"]
    assert len(r2_sql) == 3, f"expected 3 R2 hits, got {len(r2_sql)}"
    combined_sql = [f for f in res["combined"]
                    if f["name"] == "sql_string_format_deep"]
    assert len(combined_sql) == 3
    assert sorted(f["line"] for f in combined_sql) == [1, 2, 3]
