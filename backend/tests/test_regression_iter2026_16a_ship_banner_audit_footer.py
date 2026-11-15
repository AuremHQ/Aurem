"""tests/test_regression_iter2026_16a_ship_banner_audit_footer.py

BUG: the frontend "approve button didn't load" fallback banner
(`detectShipCtaFallback` / `SHIP_CTA_MENTION` in
frontend/src/components/MessageBubble.jsx) fired on every Mode E
audit/scan reply that had a fixable item, because its own CTA text
("...and I'll ship them via Mode C.") matched the frontend regex's
unbounded `ship (it|this|the)` alternative — "ship the" is a literal
substring prefix of "ship them".

This is the backend<->frontend CONTRACT test (STEP 1f): it ports the
frontend SHIP_CTA_MENTION regex into Python and asserts that
build_audit_report()'s own output text, across several fixture
mixes, never matches it. If someone edits either side (the backend
copy, or the frontend regex) in a way that reintroduces the
collision, this test catches it without needing a browser/vitest run.

The ported pattern below is kept in sync with
frontend/src/components/MessageBubble.jsx's SHIP_CTA_MENTION by hand
(no code-sharing mechanism exists between the two languages) — if
you touch one, check the other.
"""
from __future__ import annotations

import re

from services.mode_e_auditor import build_audit_report

# Ported 1:1 from frontend/src/components/MessageBubble.jsx's
# SHIP_CTA_MENTION, POST-FIX (word-boundaried, narrowed to the
# unambiguous "click the ... button" claim — see that file's comment
# for why the broader "ship it/this/the" / bare "approve the fix" /
# bare "ship via cto" alternatives were dropped as standalone,
# no-fence triggers).
SHIP_CTA_MENTION = re.compile(
    r"\bclick\b[^.]{0,40}\b(?:ship|approve)\b[^.]{0,20}\bbutton\b",
    re.IGNORECASE,
)


def _finding(severity, filepath="app.py", line=10, fix="Use parameterized queries"):
    return {
        "severity": severity,
        "filepath": filepath,
        "line": line,
        "description": f"{severity} issue",
        "fix": fix,
    }


class TestAuditFooterNeverMatchesFrontendShipCtaRegex:
    def test_only_low_severity_fixable(self):
        report = build_audit_report(
            static_findings=[], llm_findings=[], quick_wins=[_finding("low")],
            repo_ctx="acme/widgets", files_scanned=2,
        )
        assert "ship" in report.lower()  # sanity: the trigger phrase is actually present
        assert not SHIP_CTA_MENTION.search(report), report

    def test_only_medium_severity_fixable(self):
        report = build_audit_report(
            static_findings=[_finding("medium")], llm_findings=[], quick_wins=[],
            repo_ctx="acme/widgets", files_scanned=2,
        )
        assert not SHIP_CTA_MENTION.search(report), report

    def test_critical_and_high_fixable(self):
        report = build_audit_report(
            static_findings=[_finding("critical"), _finding("high", filepath="b.py", line=20)],
            llm_findings=[], quick_wins=[],
            repo_ctx="acme/widgets", files_scanned=3,
        )
        assert not SHIP_CTA_MENTION.search(report), report

    def test_no_findings_at_all(self):
        report = build_audit_report(
            static_findings=[], llm_findings=[], quick_wins=[],
            repo_ctx="acme/widgets", files_scanned=5,
        )
        assert "No major issues found" in report
        assert not SHIP_CTA_MENTION.search(report), report

    def test_findings_present_but_none_fixable_no_cta_line_at_all(self):
        unfixable = {"severity": "high", "filepath": "app.py", "description": "no fix available"}
        report = build_audit_report(
            static_findings=[unfixable], llm_findings=[], quick_wins=[],
            repo_ctx="acme/widgets", files_scanned=2,
        )
        assert "auto-fixed" not in report
        assert not SHIP_CTA_MENTION.search(report), report
