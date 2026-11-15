"""tests/test_regression_iter2026_42_part2b_finding_still_present_name_field.py

Discovered while fixing R1/R2 (Part 2b): services.vanguard_scanner::
scan_text's most common hit shapes — secrets regex matches, dangerous-
pattern matches — set ONLY "name" on each hit, never "rule_id"/"rule".
`_finding_still_present` originally checked ONLY `rule_id`/`rule` on
the RE-SCAN hits, so for those categories re-validation always
"passed" (found nothing matching) regardless of whether the LLM's
patch actually removed the problem — a silent false-negative on the
exact rule classes (hardcoded secrets, eval/exec, etc.) this whole
pipeline exists to fix.

Fix: `_finding_still_present` now also matches `f.get("name")`.
"""
from __future__ import annotations

from unittest.mock import patch

import services.finding_fix_applier as ffa


class TestFindingStillPresentNameField:
    def test_matches_a_hit_that_only_sets_name_not_rule_id(self):
        """Secrets-scanner shape: {"name": "hardcoded_secret", ...} —
        no rule_id/rule key at all. Must still be detected as a match
        against the finding's own rule_id."""
        with patch("services.vanguard_scanner.scan_text") as mock_scan:
            mock_scan.return_value = [{"name": "hardcoded_secret", "line": 3}]
            finding = {"rule_id": "hardcoded_secret", "file": "a.py", "line": 3}
            assert ffa._finding_still_present("API_KEY = 'xyz'", "a.py", finding) is True

    def test_still_matches_the_pre_existing_rule_id_and_rule_keys(self):
        """Backward compatibility — scanner shapes that DO set rule_id
        or rule must keep matching exactly as before."""
        with patch("services.vanguard_scanner.scan_text") as mock_scan:
            mock_scan.return_value = [{"rule_id": "sql_injection", "line": 5}]
            finding = {"rule_id": "sql_injection", "file": "a.py", "line": 5}
            assert ffa._finding_still_present("query", "a.py", finding) is True

        with patch("services.vanguard_scanner.scan_text") as mock_scan:
            mock_scan.return_value = [{"rule": "xss", "line": 2}]
            finding = {"rule_id": "xss", "file": "a.py", "line": 2}
            assert ffa._finding_still_present("markup", "a.py", finding) is True

    def test_no_match_when_the_name_shaped_hit_is_for_a_different_rule(self):
        """A rescan that still fires, but for an UNRELATED rule, must
        not be mistaken for the original finding still being present
        — this is what actually proves the fix resolved THIS issue."""
        with patch("services.vanguard_scanner.scan_text") as mock_scan:
            mock_scan.return_value = [{"name": "some_other_rule", "line": 9}]
            finding = {"rule_id": "hardcoded_secret", "file": "a.py", "line": 3}
            assert ffa._finding_still_present("clean now", "a.py", finding) is False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
