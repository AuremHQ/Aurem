"""
tests/test_2026_02_citation_guard_b2_tool_claims.py — Part B2
regression test: "verified via X" claims with no matching real tool
call in this turn must be blocked/stripped, not shipped as-is, and
the frequency must become measurable (unverified_tool_claims_blocked).
"""
import pytest

from services.citation_guard import CitationGuard, _extract_tool_claims


def test_extract_tool_claims_matches_the_exact_phrasing_shape():
    text = "I confirmed this is fine, verified via read_repo_file this turn."
    assert _extract_tool_claims(text) == ["read_repo_file"]


def test_extract_tool_claims_ignores_vague_prose():
    # "I double-checked this" is not a specific tool-name claim.
    assert _extract_tool_claims("I double-checked this carefully.") == []


def test_verify_ignores_a_claimed_tool_name_that_is_not_a_real_local_tool():
    # "frontend_audit" isn't in LOCAL_TOOLS — narrow by design, so an
    # unrecognized word after "verified via" never false-triggers.
    guard = CitationGuard()
    text = "That's fixed — verified via frontend_audit."
    report = guard.verify(text, tool_calls=[{"tool": "read_repo_file", "args": {}}])
    assert report["unverified_tool_claims"] == []


def test_verify_passes_when_the_claimed_tool_really_was_called():
    guard = CitationGuard()
    text = "That's fixed — verified via read_repo_file."
    report = guard.verify(text, tool_calls=[{"tool": "read_repo_file", "args": {"path": "x.py"}}])
    assert report["unverified_tool_claims"] == []


def test_verify_flags_read_repo_file_claim_when_not_actually_called():
    guard = CitationGuard()
    text = "That's fixed — verified via read_repo_file."
    report = guard.verify(text, tool_calls=[{"tool": "execute_bash", "args": {}}])
    assert report["pass"] is False
    assert "read_repo_file" in report["unverified_tool_claims"]


@pytest.mark.asyncio
async def test_enforce_strips_the_false_claim_and_flags_it_for_logging():
    guard = CitationGuard()
    ctx: dict = {}

    async def fake_llm_caller(**kwargs):
        return "That's fixed. This was not actually verified this turn."

    result = await guard.enforce(
        response_text="That's fixed — verified via read_repo_file.",
        tool_calls=[{"tool": "execute_bash", "args": {}}],
        ctx=ctx,
        llm_caller=fake_llm_caller,
    )
    assert result["retried"] is True
    assert "not actually verified" in result["text"]
    # the block was flagged on ctx for the router layer to persist
    assert len(ctx["unverified_tool_claims_blocked"]) == 1
    assert ctx["unverified_tool_claims_blocked"][0]["claimed_tools"] == ["read_repo_file"]


@pytest.mark.asyncio
async def test_enforce_leaves_a_genuinely_verified_claim_untouched():
    guard = CitationGuard()
    result = await guard.enforce(
        response_text="That's fixed — verified via read_repo_file.",
        tool_calls=[{"tool": "read_repo_file", "args": {"path": "x.py"}}],
        ctx={},
        llm_caller=lambda **kw: "unused",
    )
    assert result["retried"] is False
    assert result["text"] == "That's fixed — verified via read_repo_file."
