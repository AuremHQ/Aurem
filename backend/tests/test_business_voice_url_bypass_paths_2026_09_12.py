"""
tests/test_business_voice_url_bypass_paths_2026_09_12.py

Companion to test_business_voice_url_mangling_2026_09_12.py — locks
the TWO bypass paths the URL-preservation fix must NOT interfere with:

TEST 6 (ora_panel=True): ORA Admin/technical view intentionally
    keeps raw github.com URLs and raw filenames unfiltered.
TEST 7 (```aurem-handoff fence): the Approve-the-fix button's source
    is never touched by this filter chain per apply_business_owner_guards()'s
    top-level guard.

These are not new fixes — this file just makes the intended behavior
explicit in the regression suite so a future refactor cannot silently
regress either bypass.
"""
import asyncio

from services.business_voice_filter import (
    apply_business_voice,
    apply_business_owner_guards,
)


COMMIT_URL = (
    "https://github.com/TJSNDHU/Aurem/update/"
    "563c945bc4eb8cb9e6f28300890aa3a7d08ba5af"
)


def test_t_ora_panel_true_keeps_raw_github_url_and_filename():
    raw = f"Committed to package.json — see {COMMIT_URL}"
    out = apply_business_voice(ora_panel=True, content=raw)
    # ora_panel=True is a full bypass: filter never runs.
    assert out == raw
    assert COMMIT_URL in out
    assert "package.json" in out
    assert "website's connection" not in out.lower()
    assert "your page" not in out.lower()


def test_t_ora_panel_false_still_preserves_url_but_humanizes_prose():
    raw = f"Committed to package.json — see {COMMIT_URL}"
    out = apply_business_voice(ora_panel=False, content=raw)
    # URL is byte-for-byte preserved…
    assert COMMIT_URL in out
    # …and the filename in prose (not inside the URL) is humanized.
    assert "package.json" not in out


def test_t_aurem_handoff_fence_completely_untouched_by_guards():
    handoff = (
        "Here's the fix:\n"
        "```aurem-handoff\n"
        f"commit_url: {COMMIT_URL}\n"
        "file: package.json\n"
        "```\n"
    )
    # Top-level guard in apply_business_owner_guards() returns early
    # for any content containing an aurem-handoff fence.
    out = asyncio.get_event_loop().run_until_complete(
        apply_business_owner_guards(
            ora_panel=False, content=handoff, user_prompt="approve"
        )
    )
    assert out == handoff
    assert COMMIT_URL in out
    assert "package.json" in out


def test_t_guards_business_owner_path_preserves_url():
    """End-to-end through apply_business_owner_guards (non-panel, non-handoff)
    must still preserve the github URL verbatim."""
    raw = f"Done — commit at {COMMIT_URL}"
    out = asyncio.get_event_loop().run_until_complete(
        apply_business_owner_guards(
            ora_panel=False, content=raw, user_prompt="make the change"
        )
    )
    assert COMMIT_URL in out
    assert "website's connection.com" not in out.lower()
