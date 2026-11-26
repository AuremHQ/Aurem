"""test_audit_p0_backend_fixes.py — P0 audit fixes (2026-11-25).

Proves each backend P0 fix from the ORA audit against REAL product
code (no live model, no Mongo, no GitHub network). Follows the
agent1/agent2 repro-test pattern: fake the provider / DB at the
boundary, run the real function.

Fixes covered:
  B1  token clamp removed — council requests up to 32,000 pass through
  B2  self_heal() max_tokens 2500 -> 32000
  B4  _MAX_CALLS_PER_TOOL_PER_TURN 3 -> 10
  D1  casual_direct_reply carries a dynamic clock block
  D2  chat_with_tools base system carries a dynamic clock block
  D3  state_block state_as_of renders in the user's timezone

B3 (worker _mode_d_e fetch_file arity) and B5 (voice-contract gate)
live in test_audit_p0_voice_contract.py — they need the routers/chat
import stubs, kept separate so this file stays light.
"""
from __future__ import annotations

import asyncio
import ast
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
ORCHESTRATOR_SRC = (BACKEND_DIR / "services" / "orchestrator.py").read_text()


# ── B1: token clamp removed ──────────────────────────────────────────
# _call_llm_with_meta_inner is the real body holding the old clamp
# (was: actual_tokens = min(max_tokens, cap_for(mode))). Route every
# mode through the DeepSeek leg by leaving OPENROUTER_API_KEY unset
# (no Claude leg) and stubbing _call_deepseek on the services.llm
# namespace — the lazy import inside _inner re-resolves it per call.
async def _run_inner(monkeypatch, max_tokens, mode):
    import services.llm as llm_mod
    from services.llm._meta import _call_llm_with_meta_inner

    captured = {}

    async def _fake_deepseek(*, messages, system, max_tokens, temperature, feature=None):
        captured["max_tokens"] = max_tokens
        return "fake reply"

    monkeypatch.setattr(llm_mod, "_call_deepseek", _fake_deepseek)
    monkeypatch.setattr(llm_mod, "_openrouter_key", lambda: "")
    result = await _call_llm_with_meta_inner(
        system="s", user="u", max_tokens=max_tokens, mode=mode,
    )
    assert result["ok"] is True, result
    return captured["max_tokens"]


def test_b1_council_code_request_32000_not_clamped_to_3500(monkeypatch):
    """mode=code requesting 32,000 used to arrive at the provider as
    3,500 (cap_for('code')). Now passes through unchanged."""
    assert asyncio.run(_run_inner(monkeypatch, 32000, "code")) == 32000


def test_b1_chat_request_20000_not_clamped_to_4000(monkeypatch):
    assert asyncio.run(_run_inner(monkeypatch, 20000, "chat")) == 20000


def test_b1_analysis_request_32000_not_clamped_to_2000(monkeypatch):
    """mode=analysis had the lowest cap (2000) — the worst truncation."""
    assert asyncio.run(_run_inner(monkeypatch, 32000, "analysis")) == 32000


def test_b1_small_request_still_passes_through_unchanged(monkeypatch):
    """No clamp in either direction: a small explicit request is not
    inflated to the per-mode default."""
    assert asyncio.run(_run_inner(monkeypatch, 500, "code")) == 500


def test_b1_cap_for_defaults_still_exist_for_callers():
    """The per-mode budgets are still the documented DEFAULTS —
    only the silent override is gone."""
    from services.llm import cap_for

    assert cap_for("code") == 3500
    assert cap_for("chat") == 4000
    assert cap_for("analysis") == 2000


# ── B2: self_heal max_tokens ─────────────────────────────────────────
def test_b2_self_heal_requests_32000_not_2500(monkeypatch):
    """A large-file rewrite used to be cut at 2500 tokens, mid-syntax.
    self_heal now asks for 32,000 (the council ceiling)."""
    import services.llm as llm_mod
    from services import loop_verify

    captured = {}

    async def _fake_call_llm_with_meta(*, system, user, max_tokens, mode,
                                       user_id=None, review_mode=None, feature=None):
        captured["max_tokens"] = max_tokens
        captured["mode"] = mode
        return {"content": "x = 1\n"}

    monkeypatch.setattr(llm_mod, "call_llm_with_meta", _fake_call_llm_with_meta)
    out = asyncio.run(loop_verify.self_heal(
        {"path": "a.py", "content": "x = 0\n"}, ["E1"], "fix it",
    ))
    assert out == "x = 1"
    assert captured["max_tokens"] == 32000
    assert captured["mode"] == "code"


# ── B4: per-tool cap 3 -> 10 ─────────────────────────────────────────
def _stub_orchestrator_imports():
    """services.orchestrator's top-level imports pull tools_bridge /
    local_tools / skill_usage, which are not in this seed tree.
    Stub just those three modules (names exactly as imported) so the
    REAL orchestrator module loads; nothing under test calls them."""

    async def _async_none(*a, **k):
        return None

    async def _async_list(*a, **k):
        return []

    tb = types.ModuleType("services.tools_bridge")
    tb.list_tools = _async_list
    tb.invoke_tool = _async_none
    tb.extract_tool_calls = lambda *a, **k: []
    tb.strip_tool_calls = lambda s: s
    tb.detect_unsourced_citations = lambda *a, **k: []
    sys.modules.setdefault("services.tools_bridge", tb)
    lt = types.ModuleType("services.local_tools")
    lt.TOOL_SPECS = []
    lt.invoke_local_tool = _async_none
    lt._is_safe_repo_path = lambda *a, **k: True
    sys.modules.setdefault("services.local_tools", lt)
    su = types.ModuleType("services.skill_usage")
    su.log_skill_use = lambda *a, **k: None
    sys.modules.setdefault("services.skill_usage", su)


def test_b4_max_calls_per_tool_per_turn_is_10():
    """The template tells the model to emit 7+ read_repo_file blocks;
    the old cap of 3 aborted the turn for following it."""
    _stub_orchestrator_imports()
    import services.orchestrator as orch

    assert orch._MAX_CALLS_PER_TOOL_PER_TURN == 10


def test_b4_template_still_tells_model_7_plus_blocks():
    """Guard the other side of the contradiction: the cap must stay
    above what the template instructs, or B4 regresses silently."""
    assert "if you need 7+" in ORCHESTRATOR_SRC
    tree = ast.parse(ORCHESTRATOR_SRC)
    values = [
        node.value.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_MAX_CALLS_PER_TOOL_PER_TURN" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    assert values == [10]


# ── D1: casual reply clock ───────────────────────────────────────────
async def _run_casual(monkeypatch, prompt, user_tz):
    import services.llm as llm_mod
    from services.intent_gateway_casual_reply import casual_direct_reply

    captured = {}

    async def _fake_call_llm(messages, *, system, max_tokens, temperature, user_id=None):
        captured["system"] = system
        return "ok"

    monkeypatch.setattr(llm_mod, "call_llm", _fake_call_llm)
    monkeypatch.delenv("ORA_USER_TZ", raising=False)
    reply = await casual_direct_reply(prompt, user_id="u1", user_tz=user_tz)
    assert reply == "ok"
    return captured["system"]


def test_d1_casual_reply_carries_toronto_clock(monkeypatch):
    """'what time is it' routes to the casual tier — the system prompt
    must now carry a real clock instead of letting the model guess."""
    system = asyncio.run(_run_casual(
        monkeypatch, "what time is it right now?", "America/Toronto"))
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Toronto"))
    assert "Runtime context" in system
    assert "now_toronto" in system
    assert today.strftime("%A, %B %d, %Y") in system


def test_d1_casual_reply_honours_client_tz(monkeypatch):
    """A Kolkata client gets a Kolkata clock line, not Toronto's."""
    system = asyncio.run(_run_casual(
        monkeypatch, "what is today's date?", "Asia/Kolkata"))
    assert "now_kolkata" in system
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    assert today.strftime("%A, %B %d, %Y") in system


def test_d1_casual_reply_without_tz_falls_back_to_toronto(monkeypatch):
    """No header: Toronto (the #373 fallback), never Kolkata."""
    system = asyncio.run(_run_casual(
        monkeypatch, "what time is it?", None))
    assert "now_toronto" in system
    assert "now_kolkata" not in system


# ── D2: base-system clock ────────────────────────────────────────────
def test_d2_base_system_carries_runtime_clock(monkeypatch):
    """The primary chat/advisor base system (persona + extra +
    SECURITY + MEMORY) used to contain no date/time at all."""
    _stub_orchestrator_imports()
    from services.orchestrator import _chat_with_tools_build_base_system

    monkeypatch.delenv("ORA_USER_TZ", raising=False)
    first, followup = asyncio.run(_chat_with_tools_build_base_system(
        "what time is it right now?", "", [], "", None, [],
        user_tz="America/Toronto",
    ))
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Toronto"))
    for system in (first, followup):
        assert "Runtime context" in system
        assert "now_toronto" in system
        assert today.strftime("%A, %B %d, %Y") in system
        assert "SECURITY:" in system
        assert "MEMORY:" in system


def test_d2_base_system_clock_without_tz_falls_back_to_toronto(monkeypatch):
    _stub_orchestrator_imports()
    from services.orchestrator import _chat_with_tools_build_base_system

    monkeypatch.delenv("ORA_USER_TZ", raising=False)
    first, _followup = asyncio.run(_chat_with_tools_build_base_system(
        "hello", "", [], "", None, [],
    ))
    assert "Runtime context" in first
    assert "now_toronto" in first
    assert "now_kolkata" not in first


# ── D3: state_as_of in user tz ───────────────────────────────────────
class _FakeCursor:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeColl:
    async def count_documents(self, *a, **k):
        return 0

    def find(self, *a, **k):
        return _FakeCursor()


class _FakeDB(dict):
    def __getattr__(self, name):
        return _FakeColl()

    async def command(self, *a, **k):
        return {"ok": 1}


def _state_as_of_line(user_tz):
    from services.ora_chat_v2.state_block import build_state_block

    # _funnel_7d imports services.journey_watch at its top (outside
    # its try/except); that module is not in this seed tree and the
    # funnel numbers are irrelevant to the state_as_of clock test.
    if "services.journey_watch" not in sys.modules:
        _jw = types.ModuleType("services.journey_watch")

        async def _fake_card(db, period_days=7):
            return {"stalls_flagged": 0, "stalls_resolved": 0,
                    "hardbreaks": 0, "active_stalls": 0, "by_stage": []}

        _jw.compute_journey_watch_card = _fake_card
        sys.modules["services.journey_watch"] = _jw
    block = asyncio.run(build_state_block(_FakeDB(), user_tz=user_tz))
    lines = block.splitlines()
    assert lines[0].startswith("[SYSTEM STATE")
    return lines[1]


def test_d3_state_as_of_renders_in_toronto_time():
    """state_as_of used to be UTC-only. A Toronto turn must stamp
    Toronto time (UTC-5 in November) with the zone named."""
    line = _state_as_of_line("America/Toronto")
    assert line.startswith("state_as_of: ")
    assert "(America/Toronto)" in line
    assert line.endswith("-05:00 (America/Toronto)")
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Toronto"))
    assert today.strftime("%Y-%m-%d") in line


def test_d3_state_as_of_renders_in_kolkata_time():
    line = _state_as_of_line("Asia/Kolkata")
    assert line.endswith("+05:30 (Asia/Kolkata)")
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    assert today.strftime("%Y-%m-%d") in line


def test_d3_state_as_of_invalid_tz_falls_back_to_utc():
    line = _state_as_of_line("Not/AZone")
    assert line.endswith("+00:00 (UTC)")


def test_d3_state_as_of_without_tz_is_utc():
    line = _state_as_of_line(None)
    assert line.endswith("+00:00 (UTC)")
