"""
tests/test_audit_report_wording_2026_09_17.py — founder-reported wording fix.

The audit-report summary line said "0 files scanned" while listing 2
real findings (missing .env.example, missing Dockerfile) — reads as
self-contradictory because `files_scanned` only counts files whose
CONTENT was fetched for deep static/LLM analysis, while missing-file
checks are a separate repo-structure presence check against the file
tree and can find real issues even when 0 files were deep-scanned.
Fix: reword to "N file(s) deep-scanned" so the metric is scoped
honestly and no longer contradicts the findings listed right below it.
"""
from services.mode_e_auditor import build_audit_report


def test_zero_files_deep_scanned_with_real_quick_win_findings_is_not_contradictory():
    quick_wins = [
        {"description": "No .env.example file", "filepath": ".env.example",
         "severity": "medium", "source": "quick_win", "fix": "Create it"},
        {"description": "No Dockerfile", "filepath": "Dockerfile",
         "severity": "low", "source": "quick_win", "fix": "Create it"},
    ]
    report = build_audit_report(
        static_findings=[], llm_findings=[], quick_wins=quick_wins,
        repo_ctx="owner/repo", files_scanned=0,
    )
    summary_line = report.split("\n")[1]
    assert "0 file(s) deep-scanned" in summary_line
    assert "1 medium" in summary_line
    assert "1 low" in summary_line
    # The old wording is gone — must not claim a blanket "0 ... scanned"
    # with no scope qualifier.
    assert "0 files scanned" not in summary_line


def test_summary_line_counts_match_findings_when_files_were_deep_scanned():
    static_findings = [{"description": "x", "filepath": "a.py", "severity": "critical"}]
    report = build_audit_report(
        static_findings=static_findings, llm_findings=[], quick_wins=[],
        repo_ctx="owner/repo", files_scanned=12,
    )
    summary_line = report.split("\n")[1]
    assert "12 file(s) deep-scanned" in summary_line
    assert "1 critical" in summary_line
