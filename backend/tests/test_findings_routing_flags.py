"""
Findings fix 2026-11-25 — V2 routing flag defaults + per-flag effects.

Proves, tests-first:
  • CEO_RESCUE_ENABLED and COUNCIL_B_GLM_ENABLED now default to True
    (env unset) — the two flips in services/llm/_routing.py.
  • LONGCAT_ENABLED still defaults to False (deliberate: _LONGCAT_MODEL
    defaults to anthropic/claude-sonnet-4.5, not meituan/longcat-2.0,
    so flipping it would move code traffic to Claude at Claude prices
    with no shadow/parity data).
  • Each flipped flag's EFFECT is proven, not just its value: CEO
    rescue fires on a timed-out primary with the new default; analysis
    mode takes the GLM primary + DeepSeek rescue with the new default;
    and _call_longcat still fast-paths to GLM when LongCat is dead
    (the fallback that gates any future LongCat flip).
  • Explicit env "false" still disables each flag (rollback path).

Follows the reload pattern from test_iter212m159_parliament_v2_routing:
flag constants are import-time, so the routing module (then the shim
and services.llm) must be reloaded after monkeypatching env.
"""
import asyncio
import importlib
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _reload_routing(monkeypatch, **env):
    """Reload services.llm._routing with the given env applied (vars
    absent from env are deleted, so defaults are exercised)."""
    for k in ("LONGCAT_ENABLED", "COUNCIL_B_GLM_ENABLED", "CEO_RESCUE_ENABLED"):
        if k in env:
            monkeypatch.setenv(k, env[k])
        else:
            monkeypatch.delenv(k, raising=False)
    from services.llm import _routing as _routing_real
    importlib.reload(_routing_real)
    return _routing_real


def _reload_llm(monkeypatch, **env):
    _reload_routing(monkeypatch, **env)
    import services.llm as llm_mod
    from services import _llm_routing as _routing_shim
    importlib.reload(_routing_shim)
    importlib.reload(llm_mod)
    return llm_mod


# ─── New defaults ───────────────────────────────────────────────────

def test_new_defaults_with_env_unset(monkeypatch):
    routing = _reload_routing(monkeypatch)
    assert routing.CEO_RESCUE_ENABLED is True
    assert routing.COUNCIL_B_GLM_ENABLED is True
    assert routing.LONGCAT_ENABLED is False


def test_explicit_false_still_disables_each_flag(monkeypatch):
    routing = _reload_routing(
        monkeypatch,
        LONGCAT_ENABLED="false",
        COUNCIL_B_GLM_ENABLED="false",
        CEO_RESCUE_ENABLED="false",
    )
    assert routing.CEO_RESCUE_ENABLED is False
    assert routing.COUNCIL_B_GLM_ENABLED is False
    assert routing.LONGCAT_ENABLED is False


def test_only_literal_true_enables(monkeypatch):
    """Parsing is `.lower() == "true"` — "1"/"yes"/"on" must NOT enable."""
    routing = _reload_routing(
        monkeypatch,
        LONGCAT_ENABLED="1",
        COUNCIL_B_GLM_ENABLED="yes",
        CEO_RESCUE_ENABLED="on",
    )
    assert routing.CEO_RESCUE_ENABLED is False
    assert routing.COUNCIL_B_GLM_ENABLED is False
    assert routing.LONGCAT_ENABLED is False


# ─── Effect: CEO rescue with the NEW default ────────────────────────

def test_ceo_rescue_fires_on_timeout_with_default_on(monkeypatch):
    """Env unset → new default True → a primary that exceeds
    CEO_PRIMARY_TIMEOUT_S must trigger the DeepSeek rescue."""
    monkeypatch.setenv("CEO_PRIMARY_TIMEOUT_S", "0.05")
    llm = _reload_llm(monkeypatch)
    assert llm.CEO_RESCUE_ENABLED is True
    from core import parliament
    importlib.reload(parliament)

    calls = []

    async def fake_llm_call_protected(**kwargs):
        calls.append(kwargs)
        if kwargs["trace_name"] == "parliament.ceo.judge":
            await asyncio.sleep(0.5)
            return ("X", 500.0, None)
        return ("1", 80.0, None)

    monkeypatch.setattr(parliament.llm_call, "_llm_call_protected",
                        fake_llm_call_protected)
    content, latency, err = asyncio.run(
        parliament._ceo_judge_call_with_rescue(
            system="s", user="u", max_tokens=8,
            user_id=None, temperature=0.0, trace_metadata={"council": "A"},
        )
    )
    assert content == "1"
    assert err is None
    assert [c["trace_name"] for c in calls] == [
        "parliament.ceo.judge", "parliament.ceo.rescue"]
    assert calls[1]["mode"] == "chat"
    assert calls[1]["review_mode"] == ""


def test_ceo_rescue_off_still_single_call_when_env_false(monkeypatch):
    """Rollback path: explicit false → single call, zero rescue."""
    llm = _reload_llm(monkeypatch, CEO_RESCUE_ENABLED="false")
    assert llm.CEO_RESCUE_ENABLED is False
    from core import parliament
    importlib.reload(parliament)

    calls = []

    async def fake_llm_call_protected(**kwargs):
        calls.append(kwargs)
        return ("0", 50.0, None)

    monkeypatch.setattr(parliament.llm_call, "_llm_call_protected",
                        fake_llm_call_protected)
    content, latency, err = asyncio.run(
        parliament._ceo_judge_call_with_rescue(
            system="s", user="u", max_tokens=8,
            user_id=None, temperature=0.0, trace_metadata={"council": "A"},
        )
    )
    assert content == "0" and err is None and len(calls) == 1


# ─── Effect: Council B GLM with the NEW default ─────────────────────

def test_council_b_primary_is_glm_with_default_on(monkeypatch):
    llm = _reload_llm(monkeypatch)
    assert llm.COUNCIL_B_GLM_ENABLED is True
    assert llm.council_b_primary_model() == "z-ai/glm-5.2"


def test_analysis_mode_uses_glm_primary_with_default_on(monkeypatch):
    """mode="analysis" must hit the GLM primary (not DeepSeek) with env
    unset, and DeepSeek must NOT be called when GLM answers."""
    llm = _reload_llm(monkeypatch)
    assert llm.COUNCIL_B_GLM_ENABLED is True

    glm_calls = []
    ds_calls = []

    async def fake_glm(**kwargs):
        glm_calls.append(kwargs)
        return "glm analysis answer"

    async def fake_deepseek(**kwargs):
        ds_calls.append(kwargs)
        return "deepseek answer"

    monkeypatch.setattr(llm, "_call_glm", fake_glm)
    monkeypatch.setattr(llm, "_call_deepseek", fake_deepseek)
    from services.llm import _meta as _meta_mod
    result = asyncio.run(_meta_mod._call_llm_with_meta_inner(
        system="s", user="analyse this", mode="analysis",
    ))
    assert result["ok"] is True
    assert result["provider"] == "glm-5.2"
    assert result["content"] == "glm analysis answer"
    assert len(glm_calls) == 1
    assert ds_calls == []


def test_analysis_mode_rescues_to_deepseek_when_glm_empty(monkeypatch):
    """GLM empty → DeepSeek V3 rescue fires (the chain the flip buys)."""
    llm = _reload_llm(monkeypatch)
    assert llm.COUNCIL_B_GLM_ENABLED is True

    async def fake_glm(**kwargs):
        return ""  # GLM failed

    async def fake_deepseek(**kwargs):
        return "deepseek rescue answer"

    monkeypatch.setattr(llm, "_call_glm", fake_glm)
    monkeypatch.setattr(llm, "_call_deepseek", fake_deepseek)
    from services.llm import _meta as _meta_mod
    result = asyncio.run(_meta_mod._call_llm_with_meta_inner(
        system="s", user="analyse this", mode="analysis",
    ))
    assert result["ok"] is True
    assert result["provider"] == "deepseek-v3-council-b-rescue"
    assert result["content"] == "deepseek rescue answer"


def test_analysis_mode_stays_deepseek_when_flag_explicitly_off(monkeypatch):
    """Rollback path: explicit false → analysis rebrands to chat
    (DeepSeek only); GLM must never be called."""
    llm = _reload_llm(monkeypatch, COUNCIL_B_GLM_ENABLED="false")
    assert llm.COUNCIL_B_GLM_ENABLED is False

    glm_calls = []

    async def fake_glm(**kwargs):
        glm_calls.append(kwargs)
        return "must not be called"

    async def fake_deepseek(**kwargs):
        return "deepseek legacy answer"

    monkeypatch.setattr(llm, "_call_glm", fake_glm)
    monkeypatch.setattr(llm, "_call_deepseek", fake_deepseek)
    from services.llm import _meta as _meta_mod
    result = asyncio.run(_meta_mod._call_llm_with_meta_inner(
        system="s", user="analyse this", mode="analysis",
    ))
    assert glm_calls == []
    assert result["content"] == "deepseek legacy answer"


# ─── LongCat: stays OFF, fallback proven for a future flip ──────────

def test_longcat_still_off_with_env_unset(monkeypatch):
    llm = _reload_llm(monkeypatch)
    assert llm.LONGCAT_ENABLED is False
    assert llm.council_a_primary_model() == "z-ai/glm-5.2"


def test_call_longcat_fast_paths_to_glm_when_dead(monkeypatch):
    """The fallback that gates any future LongCat flip: LONGCAT_LIVE
    False → straight to GLM, zero OpenRouter calls. (Mirrors the
    existing test_iter212m160 contract; kept here so the flip
    decision stays test-backed in this lane's file too.)"""
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    llm = _reload_llm(monkeypatch)
    llm.LONGCAT_LIVE = False

    or_calls = []

    async def fake_openrouter(**kwargs):
        or_calls.append(kwargs)
        return "should-never-be-called"

    async def fake_glm(**kwargs):
        return "via-glm"

    from services.llm import openrouter_client as _oc
    from services.llm import openrouter_providers as _op
    monkeypatch.setattr(_oc, "call_openrouter_model", fake_openrouter)
    monkeypatch.setattr(_op, "_call_glm", fake_glm)
    try:
        out = asyncio.run(llm._call_longcat(system="s", user="u", max_tokens=10))
    finally:
        # _probes is NOT reloaded by _reload_llm, so restore the live
        # flag — otherwise later tests see a dead LongCat.
        llm.LONGCAT_LIVE = True
    assert out == "via-glm"
    assert or_calls == []
