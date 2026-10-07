"""test_2026_10_c2_longcat_mixed_tool_call_leak.py — C2 fix.

Root cause (backend/services/llm/openrouter_providers.py,
`_call_longcat`): the existing tool-call-leak guard only fell back to
GLM-5.2 when LongCat's reply was ENTIRELY a tool-call tag with zero
prose (`_stripped_len == 0`). A MIXED reply — real prose plus a
`<longcat_tool_call>...</longcat_tool_call>` tag the pipeline can't
execute (it only consumes real OpenAI-format `tool_calls`, never
LongCat's inline XML) — had `_stripped_len > 0`, so the guard never
fired and the function returned the RAW content with the tag still
embedded. That raw tag then rendered verbatim in the chat bubble
(collapsed header showing "<longcat_tool_c...") while the turn still
closed as a normal, successful reply — a promised-and-never-run tool
call read as delivered.

Fix: ANY detected tool-call tag (mixed or pure) now falls back to
GLM-5.2, which only emits real OpenAI-format tool_calls this pipeline
actually executes.
"""
from __future__ import annotations

import asyncio
import importlib
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _run(coro):
    """Isolated event loop instead of asyncio.run() — several sibling
    test files in this suite mix raw asyncio.run() with pytest-asyncio
    fixtures in the same session; asyncio.run() closes the DEFAULT
    loop on exit, which can starve a later pytest-asyncio test run in
    the same process. A throwaway loop here avoids contributing to
    that cross-file fragility."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _reload_llm():
    import services.llm as _llm
    from services import _llm_routing as _routing_shim
    from services.llm import _routing as _routing_real
    importlib.reload(_routing_real)
    importlib.reload(_routing_shim)
    importlib.reload(_llm)
    return _llm


def test_mixed_prose_plus_tool_call_falls_back_to_glm_not_leaked(monkeypatch):
    """BEFORE this fix: this exact input (real prose + a tool-call tag)
    made `_call_longcat` return the raw tag embedded in the reply.
    AFTER: it must fall back to GLM-5.2 and never leak the tag."""
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    llm = _reload_llm()
    llm.LONGCAT_LIVE = True

    leaked = (
        "Let me check that file for you.\n"
        '<longcat_tool_call>read_repo_file {"path":"backend/main.py"}</longcat_tool_call>'
    )

    async def fake_openrouter(**kwargs):
        return leaked

    async def fake_glm(**kwargs):
        return "Here's what I found in backend/main.py: ..."

    from services.llm import openrouter_client as _oc
    from services.llm import openrouter_providers as _op
    monkeypatch.setattr(_oc, "call_openrouter_model", fake_openrouter)
    monkeypatch.setattr(_op, "_call_glm", fake_glm)

    out = _run(llm._call_longcat(system="s", user="u", max_tokens=10))

    assert "longcat_tool_call" not in out, "raw tool-call tag must never reach the user"
    assert out == "Here's what I found in backend/main.py: ...", (
        "a leaked tag must fall back to a real GLM-5.2 answer, not a "
        "truncated/leaked fragment"
    )


def test_mixed_reply_does_NOT_flip_longcat_live_false(monkeypatch):
    """A mixed reply still had real prose — one leaked tag alongside
    it isn't proof LongCat itself is down, so (unlike the pure
    tool-call-only case) LONGCAT_LIVE must stay True for the next
    call."""
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    llm = _reload_llm()
    llm.LONGCAT_LIVE = True

    async def fake_openrouter(**kwargs):
        return 'Sure.\n<longcat_tool_call>list_repo_files {}</longcat_tool_call>'

    async def fake_glm(**kwargs):
        return "via-glm"

    from services.llm import openrouter_client as _oc
    from services.llm import openrouter_providers as _op
    monkeypatch.setattr(_oc, "call_openrouter_model", fake_openrouter)
    monkeypatch.setattr(_op, "_call_glm", fake_glm)

    out = _run(llm._call_longcat(system="s", user="u", max_tokens=10))
    assert out == "via-glm"
    assert llm.LONGCAT_LIVE is True, (
        "a mixed prose+tag reply must NOT flip LONGCAT_LIVE — only the "
        "pure tool-call-only shape does that"
    )


def test_pure_tool_call_only_still_flips_live_false(monkeypatch):
    """Pre-existing contract (2026-02-18 fix) must still hold: a reply
    that is ENTIRELY a tool-call tag (no prose at all) still flips
    LONGCAT_LIVE=False, same as before this change."""
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    llm = _reload_llm()
    llm.LONGCAT_LIVE = True

    async def fake_openrouter(**kwargs):
        return '<longcat_tool_call>read_repo_file {"path":"x"}</longcat_tool_call>'

    async def fake_glm(**kwargs):
        return "via-glm"

    from services.llm import openrouter_client as _oc
    from services.llm import openrouter_providers as _op
    monkeypatch.setattr(_oc, "call_openrouter_model", fake_openrouter)
    monkeypatch.setattr(_op, "_call_glm", fake_glm)

    out = _run(llm._call_longcat(system="s", user="u", max_tokens=10))
    assert out == "via-glm"
    assert llm.LONGCAT_LIVE is False
    # Restore — this test intentionally flips the module-level flag;
    # leaving it False would pollute any test that runs after this
    # one in the same pytest session (reload() doesn't always re-pick
    # the env default on a plain attribute mutation).
    llm.LONGCAT_LIVE = True


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
