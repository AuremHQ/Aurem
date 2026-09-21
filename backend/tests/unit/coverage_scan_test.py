"""
tests/unit/coverage_scan_test.py — 2026-09-21 decision-point audit lock-in.

Confirmed via code audit: coverage-scan pass/flag/escalate is already
fully deterministic (regex-parse of pytest's own summary line +
threshold counting via `_parse_pytest_summary()` /
`classify_pytest_summary()` in services/health_coverage_scan.py) — no
LLM involved, no LLM integration needed. This file locks that
behavior in so a future refactor can't silently break it.
"""
from __future__ import annotations

from services.health_coverage_scan import _parse_pytest_summary, classify_pytest_summary


def test_zero_passed_zero_failed_classifies_as_fail():
    summary = _parse_pytest_summary("0 passed, 0 failed in 0.42s")
    assert summary == {"passed": 0, "failed": 0, "errors": 0}
    assert classify_pytest_summary(summary) == "fail"


def test_real_passing_summary_with_coverage_classifies_as_pass():
    text = (
        "............................\n"
        "===== 855 passed, 32 failed, 3 error in 428.11s (0:07:08) ====="
    )
    summary = _parse_pytest_summary(text)
    assert summary == {"passed": 855, "failed": 32, "errors": 3}
    assert classify_pytest_summary(summary) == "pass"


def test_log_noise_with_port_number_does_not_pollute_error_count():
    """Regression for the 2026-09-07 bug this module's docstring
    mentions: a digit-adjacent-to-'error' substring in unrelated log
    noise (e.g. a Redis port warning) must never be picked up as a
    test-error count — only the FINAL pytest summary line is read."""
    text = (
        "WARNING rate-limiter: could not connect to redis on port 6379 error\n"
        "10 passed, 0 failed in 1.10s"
    )
    summary = _parse_pytest_summary(text)
    assert summary["errors"] == 0
    assert classify_pytest_summary(summary) == "pass"


def test_errors_only_run_is_still_a_nonzero_total_so_not_a_fail_verdict():
    """`classify_pytest_summary` only distrusts a TOTAL of zero
    (nothing observed at all) — a run that surfaced only collection
    errors still has a nonzero total and is a real, trustable run."""
    summary = {"passed": 0, "failed": 0, "errors": 5}
    assert classify_pytest_summary(summary) == "pass"


def test_missing_summary_line_entirely_parses_to_all_zero_and_fails():
    summary = _parse_pytest_summary("some unrelated crash traceback with no summary line")
    assert summary == {"passed": 0, "failed": 0, "errors": 0}
    assert classify_pytest_summary(summary) == "fail"
