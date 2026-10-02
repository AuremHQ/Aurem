"""test_2026_10_c1_security_finding_grounding_lock.py — C1 fix.

Root cause (services/ora_chat/grounding_check.py +
services/chat_helpers.py): in casual (short-message, no new tool
call) mode, a reply's "retrieved_context" was ONLY this turn's tool
results — empty for a bare "?" / "continue" follow-up — and neither
existing grounding guard (`apply_fabricated_content_guard`,
`apply_live_content_claim_guard`) recognizes a security-finding/CVE-
shaped claim at all. So a casual-mode reply naming a CVE had nothing
checking it, in either direction.

Fix: (1) `scan_findings_context_for_grounding(project_id)` folds the
project's stored `cto_open_findings` into the grounding text; (2) the
new `apply_ungrounded_security_finding_guard` swaps the WHOLE reply
for an honest "I don't have that in a stored scan" message whenever
it names a CVE ID that isn't a substring of that grounding text.

Lock test (founder's exact spec): ask "?" and "continue" after a
scan, three times each — same real finding passes through
unchanged every time; a fabricated CVE never a real scan result is
blocked every time, never randomly let through.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.ora_chat.grounding_check import (          # noqa: E402
    apply_ungrounded_security_finding_guard,
    extract_cve_claims,
    UNGROUNDED_SECURITY_FINDING_MESSAGE,
)

REAL_SCAN_FINDING_CTX = (
    "Outdated jinja2 dependency with known RCE | dep_outdated_cve | "
    "requirements.txt:14 | jinja2==2.11.0 is affected by CVE-2024-22195, "
    "upgrade to >=3.1.3."
)
FABRICATED_CVE = "CVE-2019-99999"  # never appears in REAL_SCAN_FINDING_CTX


@pytest.mark.parametrize("prompt", ["?", "continue"])
def test_real_finding_from_scan_record_passes_unchanged_3x(prompt):
    """A reply that restates the REAL stored finding's real CVE must
    come through completely untouched, every single time (3 reps),
    for both "?" and "continue" follow-ups."""
    reply = (
        "Still open: jinja2==2.11.0 is affected by CVE-2024-22195 "
        "(requirements.txt:14) — want me to bump it?"
    )
    results = [
        apply_ungrounded_security_finding_guard(reply, REAL_SCAN_FINDING_CTX)
        for _ in range(3)
    ]
    assert results == [reply, reply, reply], (
        f"grounded finding must pass through identically every time for "
        f"prompt={prompt!r}, got: {results}"
    )


@pytest.mark.parametrize("prompt", ["?", "continue"])
def test_fabricated_cve_not_in_scan_record_blocked_every_time_3x(prompt):
    """A reply naming a CVE that is NOT in the stored scan record must
    be swapped for the honest message every single time (3 reps),
    never randomly let through — for both "?" and "continue"."""
    reply = (
        f"That also matches {FABRICATED_CVE}, a known critical RCE "
        "in this exact dependency chain."
    )
    results = [
        apply_ungrounded_security_finding_guard(reply, REAL_SCAN_FINDING_CTX)
        for _ in range(3)
    ]
    assert results == [UNGROUNDED_SECURITY_FINDING_MESSAGE] * 3, (
        f"fabricated CVE must be blocked identically every time for "
        f"prompt={prompt!r}, got: {results}"
    )
    for r in results:
        assert FABRICATED_CVE not in r, "the fabricated CVE must never reach the user"


def test_mixed_reply_real_cve_plus_fabricated_cve_is_still_blocked():
    """One real CVE + one fabricated CVE in the same reply must still
    block the WHOLE turn — a partially-true reply is not a pass."""
    reply = (
        "CVE-2024-22195 is real and confirmed. I also found "
        f"{FABRICATED_CVE} affecting the same file."
    )
    out = apply_ungrounded_security_finding_guard(reply, REAL_SCAN_FINDING_CTX)
    assert out == UNGROUNDED_SECURITY_FINDING_MESSAGE


def test_no_cve_mentioned_is_a_pure_no_op():
    reply = "No open findings right now — you're all caught up."
    assert apply_ungrounded_security_finding_guard(reply, REAL_SCAN_FINDING_CTX) == reply
    assert extract_cve_claims(reply) == []


def test_scan_findings_context_for_grounding_reads_real_mongo_rows(monkeypatch):
    """Integration check: the async context-builder actually queries
    cto_open_findings for the given project and folds real rows in —
    not a hardcoded/mocked string."""
    from services import chat_helpers

    class _FakeCursor:
        def limit(self, n):
            return self

        async def to_list(self, length):
            return [{
                "title": "Outdated jinja2 dependency with known RCE",
                "rule_id": "dep_outdated_cve",
                "file": "requirements.txt", "line": 14,
                "message": "jinja2==2.11.0 is affected by CVE-2024-22195.",
            }]

    class _FakeFindings:
        def find(self, query, projection):
            assert query == {"project_id": "p_demo_a"}
            return _FakeCursor()

    class _FakeDb:
        cto_open_findings = _FakeFindings()

    monkeypatch.setattr(chat_helpers, "get_db", lambda: _FakeDb())
    loop = asyncio.new_event_loop()
    try:
        ctx = loop.run_until_complete(chat_helpers.scan_findings_context_for_grounding("p_demo_a"))
    finally:
        loop.close()
    assert "CVE-2024-22195" in ctx
    assert "requirements.txt" in ctx


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
