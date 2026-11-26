"""
tests/test_part89_self_heal.py — Part IX audit fix (E):
core/parliament/self_heal.py (a) >6000-char truncation data loss,
(b) output=None TypeError, (c) hardcoded review_mode="pro".

Offline unit tests — the real SelfHeal is loaded with its LLM call
stubbed (core.parliament.llm_call._llm_call_protected); the fake
"LLM" echoes the file it was sent with the lint error fixed, so a
test can prove the tail survives. Run from backend/ with the agent1
venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part89_self_heal.py -q
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_PARLIAMENT_DIR = _BACKEND_DIR / "core" / "parliament"


def _strip_fences(content: str) -> str:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class _FakeBreaker:
    def should_attempt(self):
        return True


@pytest.fixture()
def healer(monkeypatch):
    """Load the REAL SelfHeal with a fake LLM call. Returns
    (healer_instance, calls_list, fake_fn)."""
    if "core" not in sys.modules:
        core_pkg = types.ModuleType("core")
        core_pkg.__path__ = [str(_BACKEND_DIR / "core")]
        sys.modules["core"] = core_pkg
    if "core.parliament" not in sys.modules:
        parl_pkg = types.ModuleType("core.parliament")
        parl_pkg.__path__ = [str(_PARLIAMENT_DIR)]
        sys.modules["core.parliament"] = parl_pkg

    calls: list[dict] = []

    async def _fake_llm(*, system, user, max_tokens, mode, review_mode,
                        temperature, trace_name, trace_metadata):
        calls.append({"system": system, "user": user,
                      "max_tokens": max_tokens, "mode": mode,
                      "review_mode": review_mode,
                      "temperature": temperature})
        # "Fix" the file the healer sent us: the CURRENT CONTENT
        # section, with the deliberate error repaired.
        m = re.search(r"--- CURRENT CONTENT.*?\n(.*?)\n--- END CONTENT ---",
                      user, re.DOTALL)
        content = m.group(1) if m else ""
        return content.replace("BROKEN", "FIXED"), 1.0, None

    llm_call = types.ModuleType("core.parliament.llm_call")
    llm_call._llm_call_protected = _fake_llm
    monkeypatch.setitem(sys.modules, "core.parliament.llm_call", llm_call)

    breaker_mod = types.ModuleType("core.parliament.breaker")
    breaker_mod._GLOBAL_BREAKER = _FakeBreaker()
    monkeypatch.setitem(sys.modules, "core.parliament.breaker", breaker_mod)

    scoring_mod = types.ModuleType("core.parliament.scoring")
    scoring_mod._strip_fences = _strip_fences
    monkeypatch.setitem(sys.modules, "core.parliament.scoring", scoring_mod)

    spec = importlib.util.spec_from_file_location(
        "core.parliament.self_heal", _PARLIAMENT_DIR / "self_heal.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["core.parliament.self_heal"] = mod
    spec.loader.exec_module(mod)
    return mod.SelfHeal(), calls, _fake_llm


def _big_file(n_chars: int = 9000) -> str:
    """A file with a lint error near the top and a unique sentinel
    in the last 500 chars — the exact shape the old [:6000] cut
    destroyed."""
    head = "import os\n\n\ndef broken_fn():\n    BROKEN\n\n\n"
    sentinel = ("\ndef _sentinel_tail_marker_xyz():\n    return 42\n"
                "\nTAIL_CONSTANT_XYZ = 'survive-me-12345'\n")
    filler_len = n_chars - len(head) - len(sentinel)
    parts: list[str] = []
    total = 0
    i = 0
    while total < filler_len:
        line = f"# filler line {i:05d} xxxxxxxxxx\n"
        parts.append(line)
        total += len(line)
        i += 1
    filler = "".join(parts)[:filler_len]
    body = head + filler + sentinel
    assert len(body) >= n_chars - 50, len(body)
    return body


# ── (a) whole-file heal ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_over_6000_chars_survives_heal_intact(healer):
    """The core regression: a ~9,000-char file used to lose
    everything past char 6000. The healed output must keep the tail
    sentinel, the trailing constant, and every top-level def."""
    heal_obj, calls, _ = healer
    content = _big_file(9000)
    res = await heal_obj.heal(
        task="Fix the lint error.", round_num=0,
        all_attempts=[{"output": content, "score": 0.0,
                       "error": "E999 syntax error line 5"}])
    assert res["status"] == "retry"
    out = res["output"]
    assert "FIXED" in out and "BROKEN" not in out
    assert "def _sentinel_tail_marker_xyz():" in out
    assert "TAIL_CONSTANT_XYZ = 'survive-me-12345'" in out
    assert "def broken_fn():" in out
    assert len(out) >= len(content) - 100


@pytest.mark.asyncio
async def test_llm_receives_whole_file_and_scaled_token_cap(healer):
    heal_obj, calls, _ = healer
    content = _big_file(9000)
    await heal_obj.heal(
        task="Fix it.", round_num=0,
        all_attempts=[{"output": content, "score": 0.0, "error": "E1"}])
    sent = calls[0]["user"]
    assert "TAIL_CONSTANT_XYZ" in sent, "model must SEE the tail to keep it"
    assert calls[0]["max_tokens"] >= 5000  # scaled, not the flat 2500


@pytest.mark.asyncio
async def test_oversized_file_escalates_instead_of_truncating(healer):
    heal_obj, calls, _ = healer
    content = "x = 1\n" * 12000  # ~72,000 chars
    res = await heal_obj.heal(
        task="Fix it.", round_num=0,
        all_attempts=[{"output": content, "score": 0.0, "error": "E1"}])
    assert res["status"] == "escalate"
    assert res["output"] is None
    assert "file_too_large" in res["reason"]
    assert calls == [], "LLM must never see a truncated file"


@pytest.mark.asyncio
async def test_much_shorter_rewrite_is_rejected(healer, monkeypatch):
    """Tripwire: a heal that returns <50% of the original is data
    loss, not a fix — escalate with the original intact."""
    heal_obj, calls, fake = healer

    async def _shrinking_llm(*, system, user, max_tokens, mode,
                             review_mode, temperature, trace_name,
                             trace_metadata):
        calls.append({"user": user, "max_tokens": max_tokens,
                      "review_mode": review_mode})
        return "x = 1\n", 1.0, None

    # self_heal captured the ORIGINAL llm_call module object at
    # import (`from . import llm_call`); patch its attribute directly
    # so the fake above is what heal() calls.
    import core.parliament.self_heal as sh_mod
    monkeypatch.setattr(sh_mod._llm_call_mod, "_llm_call_protected",
                        _shrinking_llm)
    content = _big_file(9000)
    res = await heal_obj.heal(
        task="Fix it.", round_num=0,
        all_attempts=[{"output": content, "score": 0.0, "error": "E1"}])
    assert res["status"] == "escalate"
    assert res["output"] is None
    assert "much_shorter" in res["reason"]


# ── (b) None output/error ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_none_output_and_error_do_not_crash(healer):
    heal_obj, calls, _ = healer
    res = await heal_obj.heal(
        task="Fix it.", round_num=0,
        all_attempts=[{"output": None, "score": 0.0, "error": None}])
    # No TypeError: the None path is guarded; the fake LLM returns
    # an empty echo, so the heal escalates on empty output.
    assert res["status"] == "escalate"
    assert res["reason"] == "empty_output"


# ── (c) review_mode threading ────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_round_heal_uses_swift_not_pro(healer):
    heal_obj, calls, _ = healer
    await heal_obj.heal(
        task="Fix it.", round_num=0,
        all_attempts=[{"output": "x = BROKEN\n", "score": 0.0,
                       "error": "E1"}])
    assert calls[0]["review_mode"] == "swift"


@pytest.mark.asyncio
async def test_later_round_heal_escalates_to_pro(healer):
    heal_obj, calls, _ = healer
    await heal_obj.heal(
        task="Fix it.", round_num=1,
        all_attempts=[{"output": "x = BROKEN\n", "score": 0.0,
                       "error": "E1"}])
    assert calls[0]["review_mode"] == "pro"


@pytest.mark.asyncio
async def test_explicit_review_mode_is_respected(healer):
    heal_obj, calls, _ = healer
    await heal_obj.heal(
        task="Fix it.", round_num=0, review_mode="maxx",
        all_attempts=[{"output": "x = BROKEN\n", "score": 0.0,
                       "error": "E1"}])
    assert calls[0]["review_mode"] == "maxx"
