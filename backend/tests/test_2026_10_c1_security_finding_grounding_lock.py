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
    extract_security_finding_claims,
    UNGROUNDED_SECURITY_FINDING_MESSAGE,
)

REAL_SCAN_FINDING_CTX = (
    "Outdated jinja2 dependency with known RCE | dep_outdated_cve | "
    "requirements.txt:14 | jinja2==2.11.0 is affected by CVE-2024-22195, "
    "upgrade to >=3.1.3."
)
FABRICATED_CVE = "CVE-2019-99999"  # never appears in REAL_SCAN_FINDING_CTX

# ─── 2026-10 round 2 — founder's real repro set was NOT only CVEs ───
# Real stored finding this time: a rate-limit gap AND a bcrypt
# version note, both genuinely in the scan record this turn.
REAL_NON_CVE_CTX = (
    "Login endpoint has no rate limit, allowing roughly 1000x more "
    "attempts than a normal brute-force guard permits | "
    "auth_rate_limit_missing | routers/auth.py:88 | "
    "bcrypt 4.0.1 -> 4.1.2 is also recommended, current hashing is fine."
)
# Fabricated variants — plausible-sounding, but NOT the stored finding.
FABRICATED_RATE_LIMIT = "the rate limit is broken, allowing 5000x more requests than intended"
FABRICATED_VERSION_BUMP = "bcrypt 5.0.0 -> 5.1.0 fixes a timing side-channel"
FABRICATED_WEAK_SECRET = "the JWT keys are weak and can be brute-forced offline"


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


@pytest.mark.parametrize("prompt", ["?", "continue"])
@pytest.mark.parametrize("real_reply", [
    "Still open: no rate limit on login, ~1000x more attempts than a "
    "normal guard allows (routers/auth.py:88) — want me to add one?",
    "Also noted: bcrypt 4.0.1 -> 4.1.2 is recommended (current hashing is fine).",
], ids=["rate-limit-claim", "version-bump-claim"])
def test_real_non_cve_finding_passes_unchanged_3x(prompt, real_reply):
    """Founder's actual repro set: rate-limit/version-bump claims that
    genuinely match the stored finding must pass through untouched,
    every time, for both "?" and "continue"."""
    results = [
        apply_ungrounded_security_finding_guard(real_reply, REAL_NON_CVE_CTX)
        for _ in range(3)
    ]
    assert results == [real_reply, real_reply, real_reply], (
        f"grounded non-CVE finding must pass through identically every "
        f"time for prompt={prompt!r}, got: {results}"
    )


@pytest.mark.parametrize("prompt", ["?", "continue"])
@pytest.mark.parametrize("fabricated_reply", [
    FABRICATED_RATE_LIMIT, FABRICATED_VERSION_BUMP, FABRICATED_WEAK_SECRET,
], ids=["rate-limit", "version-bump", "weak-secret"])
def test_fabricated_non_cve_finding_blocked_every_time_3x(prompt, fabricated_reply):
    """Founder's exact failures: "broken rate limits ... 1000x",
    "outdated bcrypt 5.0.0 -> 5.1.0", "weak JWT keys" — none of these
    name a CVE, so the old CVE-only gate let all three through. Each
    must now be blocked identically 3/3, for both "?" and "continue"."""
    results = [
        apply_ungrounded_security_finding_guard(fabricated_reply, REAL_NON_CVE_CTX)
        for _ in range(3)
    ]
    assert results == [UNGROUNDED_SECURITY_FINDING_MESSAGE] * 3, (
        f"fabricated non-CVE finding must be blocked identically every "
        f"time for prompt={prompt!r}, got: {results}"
    )


def test_vague_file_reference_with_no_real_backing_is_still_caught_via_other_claims():
    """Founder also saw a finding pointed at vaguely as "that file"
    with no real path. On its own, "that file" names nothing concrete
    to verify — but it never travels alone: it always rides alongside
    a hard-trigger claim (CVE / version-bump / weak-secret / rate-
    limit-magnitude), and THAT claim is what the guard blocks on. This
    locks the actual founder sentence end-to-end."""
    reply = (
        "That also has weak JWT keys — I found it in that file, same "
        "RCE class as before."
    )
    out = apply_ungrounded_security_finding_guard(reply, REAL_NON_CVE_CTX)
    assert out == UNGROUNDED_SECURITY_FINDING_MESSAGE
    assert "that file" not in out


def test_extract_security_finding_claims_covers_all_four_shapes():
    reply = (
        f"{FABRICATED_CVE}, {FABRICATED_RATE_LIMIT}, "
        f"{FABRICATED_VERSION_BUMP}, {FABRICATED_WEAK_SECRET}"
    )
    claims = extract_security_finding_claims(reply)
    assert any(FABRICATED_CVE in c for c in claims)
    assert any("5000x" in c for c in claims)
    assert any("5.0.0" in c and "5.1.0" in c for c in claims)  # version pair captured
    assert any("weak" in c.lower() and "jwt" in c.lower() for c in claims)


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
