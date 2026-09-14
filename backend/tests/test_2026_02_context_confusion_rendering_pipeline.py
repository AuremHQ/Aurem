"""
tests/test_2026_02_context_confusion_rendering_pipeline.py - Part D
of the architectural "Context Confusion" fix (2026-02).

services.chat_helpers.run_rendering_pipeline is the new single
owner for the full post-model content-rendering sequence (previously
hand-copied separately into routers/chat/turn.py AND
routers/chat/stream.py: apply_output_guards -> strip_machinery_leak /
enforce_length_cap -> apply_business_owner_guards). These tests
exercise that ONE shared function end to end (not the individual
guards in isolation, which already have their own dedicated tests -
see test_business_voice_nonpage_filename_2026_09_12.py and
test_business_voice_file_page_label_protection_2026_02.py) to prove
the consolidated pipeline still produces the CORRECT term end to
end, and that per-pass tracing is real, not decorative.

Per the founder's explicit instruction, this is a PRESENCE-based
check ("that file" must actually appear), not merely an absence-based
one ("that page" must not appear) - an absence-only test would have
let the exact word-map-collision regression this engagement already
found (business_voice_filter.py's generic "file" -> "page" mapping
silently overwriting a correct "that file") pass unnoticed.
"""
import pytest

from services.chat_helpers import run_rendering_pipeline


@pytest.mark.asyncio
async def test_pipeline_end_to_end_lands_on_that_file_not_that_page():
    raw = "Let me check package.json first, then update it for you."
    content, trace = await run_rendering_pipeline(
        "add a comment to package.json", raw, False, "",
        skip=False, tool_calls_run=0, session_id="s1", user_id="u1",
        plain_english_active=False, ora_panel=False,
    )
    assert "package.json" not in content
    assert "that file" in content.lower()          # presence, not just absence
    assert "that page" not in content.lower()


@pytest.mark.asyncio
async def test_pipeline_records_a_trace_entry_per_pass():
    raw = "Let me check main.py first."
    _, trace = await run_rendering_pipeline(
        "check main.py", raw, False, "",
        skip=False, tool_calls_run=0, session_id="s1", user_id="u1",
        plain_english_active=False, ora_panel=False,
    )
    pass_names = [p["pass"] for p in trace["passes"]]
    assert "apply_output_guards" in pass_names
    assert "apply_business_owner_guards" in pass_names
    # the business-owner pass actually changed the text (main.py -> "that file")
    bvf = next(p for p in trace["passes"] if p["pass"] == "apply_business_owner_guards")
    assert bvf["changed"] is True


@pytest.mark.asyncio
async def test_pipeline_never_touches_a_real_aurem_handoff_fence():
    raw = "Here's what I found.\n```aurem-handoff\nFix main.py.\n```\nDone."
    content, trace = await run_rendering_pipeline(
        "ship it", raw, False, "",
        skip=False, tool_calls_run=0, session_id="s1", user_id="u1",
        plain_english_active=False, ora_panel=False,
    )
    assert "```aurem-handoff" in content
    assert "Fix main.py." in content    # untouched, byte-for-byte, inside the fence
