"""tests/test_regression_iter2026_53_part2c_c4_legacy_field_sanitization.py

Part 2c C4(b) [Low] — `reconcile_findings_against_stored_rows`
(services/finding_scope.py) passes an UNMATCHED legacy `findings[]`
item through completely UNCHANGED — including its client-supplied
`title`/`message`/`fix_hint` (free text, already fenced as untrusted
for the LLM prompt — that defense stays) and `severity` (used for
billing category cost AND for sort/display ordering elsewhere). None
of those fields were ever capped in length, stripped of control
characters, or validated against the known severity enum.

Fix: an item with no matching stored row is still passed through
(R1's presence pre-check is what decides whether it's real — this
function's job is only field trust, not existence), but its free-text
fields are now capped (2000 chars) and stripped of control characters,
and `severity` is restricted to the known enum
(critical/high/medium/low/info) — anything else becomes "medium"
rather than an arbitrary client-supplied string flowing into the
category-cost lookup / sort key.
"""
from __future__ import annotations

import pytest

from services.finding_scope import reconcile_findings_against_stored_rows

pytestmark = pytest.mark.asyncio


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []

    def find(self, filt, proj=None):
        matched = [dict(d) for d in self.docs
                   if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict))]

        class _Cur:
            def __init__(self, items):
                self.items = items

            async def to_list(self, length=None):
                return list(self.items)[: (length if length is not None else len(self.items))]
        return _Cur(matched)


class _FakeDB:
    def __init__(self):
        self.cto_open_findings = _FakeColl([])  # no stored rows at all — everything is "unmatched"

    def __getitem__(self, name):
        return getattr(self, name)


class TestC4LegacyFieldSanitization:
    async def test_oversized_title_is_capped(self):
        db = _FakeDB()
        huge_title = "A" * 10_000
        out = await reconcile_findings_against_stored_rows(
            db, user_id="u1", project_id="p1",
            findings=[{"id": "fresh1", "title": huge_title, "severity": "high"}],
        )
        assert len(out) == 1
        assert len(out[0]["title"]) <= 2000

    async def test_control_characters_are_stripped_from_free_text(self):
        db = _FakeDB()
        hostile = "Hardcoded secret\x00\x01\x1b[31m in config\x07"
        out = await reconcile_findings_against_stored_rows(
            db, user_id="u1", project_id="p1",
            findings=[{"id": "fresh2", "title": hostile, "message": hostile,
                       "fix_hint": hostile, "severity": "high"}],
        )
        assert len(out) == 1
        for field in ("title", "message", "fix_hint"):
            value = out[0][field]
            assert "\x00" not in value
            assert "\x01" not in value
            assert "\x1b" not in value
            assert "\x07" not in value
        assert "Hardcoded secret" in out[0]["title"]

    async def test_unicode_title_is_preserved_within_the_cap(self):
        """Sanitization must not mangle legitimate unicode content —
        only cap length and strip control bytes."""
        db = _FakeDB()
        unicode_title = "🔒 セキュリティ上の問題 — énoncé spécial"
        out = await reconcile_findings_against_stored_rows(
            db, user_id="u1", project_id="p1",
            findings=[{"id": "fresh3", "title": unicode_title, "severity": "medium"}],
        )
        assert out[0]["title"] == unicode_title

    @pytest.mark.parametrize("bogus_severity", [
        "DROP TABLE findings;--", "{'$ne': None}", "CRITICAL!!!", "", None, 12345,
    ])
    async def test_invalid_severity_values_are_normalized_to_a_safe_default(self, bogus_severity):
        db = _FakeDB()
        out = await reconcile_findings_against_stored_rows(
            db, user_id="u1", project_id="p1",
            findings=[{"id": "fresh4", "title": "t", "severity": bogus_severity}],
        )
        assert out[0]["severity"] in ("critical", "high", "medium", "low", "info")

    @pytest.mark.parametrize("good_severity", ["critical", "high", "medium", "low", "info"])
    async def test_valid_severity_values_pass_through_unchanged(self, good_severity):
        db = _FakeDB()
        out = await reconcile_findings_against_stored_rows(
            db, user_id="u1", project_id="p1",
            findings=[{"id": "fresh5", "title": "t", "severity": good_severity}],
        )
        assert out[0]["severity"] == good_severity

    async def test_matched_stored_row_fields_are_not_double_sanitized_or_altered(self):
        """A MATCHED item is overwritten from the stored row (the
        existing, already-correct contract) — sanitization only
        applies to the unmatched pass-through path, never mangling
        the trusted stored data."""
        db = _FakeDB()
        db.cto_open_findings.docs.append({
            "finding_id": "real1", "rule_id": "r1", "file": "a.py", "line": 1,
            "severity": "critical", "title": "Real stored title", "message": "m",
            "fix_hint": "h", "category": "security", "status": "open",
            "user_id": "u1", "project_id": "p1",
        })
        out = await reconcile_findings_against_stored_rows(
            db, user_id="u1", project_id="p1",
            findings=[{"id": "real1", "severity": "low", "title": "tampered"}],
        )
        assert out[0]["severity"] == "critical"
        assert out[0]["title"] == "Real stored title"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
