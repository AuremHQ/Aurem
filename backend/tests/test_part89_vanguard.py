"""
tests/test_part89_vanguard.py — Part IX audit fix (C):
vanguard_verify_agent.py bypasses (a)–(d).

Offline unit tests — the real module is loaded with its heavy
imports stubbed (vanguard_scanner, llm, brain_gateway,
loop_token_ledger, vanguard_config, sandbox_runner); the LLM and
sandbox are fakes controlled per test. Run from backend/ with the
agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part89_vanguard.py -q
"""
from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import tempfile
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SERVICES_DIR = _BACKEND_DIR / "services"


def _ensure_services_package():
    if "services" not in sys.modules:
        pkg = types.ModuleType("services")
        pkg.__path__ = [str(_SERVICES_DIR)]
        sys.modules["services"] = pkg


def _load_vanguard(monkeypatch):
    """Load the REAL vanguard_verify_agent.py with stubbed deps.
    Returns (module, stubs dict)."""
    _ensure_services_package()

    scanner = types.ModuleType("services.vanguard_scanner")
    scanner.scan_file_blocks = lambda blocks: []
    scanner.has_critical = lambda findings: any(
        (f or {}).get("severity") == "CRITICAL" for f in (findings or []))
    # 2026-11-27 Vanguard 28 — verify_agent imports LOW_SECRET_IDS +
    # ALL_RULE_IDS from the scanner at module load, so the stub must
    # carry them. Values are copied from the REAL scanner file (light,
    # side-effect-free) so _apply_level_scope behaves identically if
    # a test ever runs at LOW — never hand-maintain a second copy.
    _real_spec = importlib.util.spec_from_file_location(
        "_vanguard_scanner_real", _SERVICES_DIR / "vanguard_scanner.py")
    assert _real_spec is not None and _real_spec.loader is not None
    _real_scanner = importlib.util.module_from_spec(_real_spec)
    _real_spec.loader.exec_module(_real_scanner)
    scanner.LOW_SECRET_IDS = _real_scanner.LOW_SECRET_IDS
    scanner.ALL_RULE_IDS = _real_scanner.ALL_RULE_IDS
    monkeypatch.setitem(sys.modules, "services.vanguard_scanner", scanner)

    llm_pkg = types.ModuleType("services.llm")
    llm_pkg.__path__ = []
    llm_pkg._deepseek_model = lambda: "deepseek/deepseek-chat"

    async def _default_model(*args, **kwargs):
        return '{"pass": true, "findings": [], "summary": "ok"}'

    llm_pkg.call_openrouter_model = _default_model
    monkeypatch.setitem(sys.modules, "services.llm", llm_pkg)

    providers = types.ModuleType("services.llm.openrouter_providers")
    providers._CLAUDE_MODEL = "anthropic/claude-sonnet-4.5"
    monkeypatch.setitem(
        sys.modules, "services.llm.openrouter_providers", providers)

    brain = types.ModuleType("services.brain_gateway")

    async def _record_wrap(coro, **kwargs):
        return await coro

    brain.record_wrap = _record_wrap
    monkeypatch.setitem(sys.modules, "services.brain_gateway", brain)

    ledger = types.ModuleType("services.loop_token_ledger")
    ledger.current_ambient_user_id = lambda: ""
    ledger.current_surface_context = lambda: {}

    @asynccontextmanager
    async def _null_ctx(*args, **kwargs):
        yield

    ledger.llm_user_context = _null_ctx
    ledger.llm_surface_context = _null_ctx
    monkeypatch.setitem(sys.modules, "services.loop_token_ledger", ledger)

    config = types.ModuleType("services.vanguard_config")

    async def _mode_settings(mode="swift"):
        return True, "CRITICAL"

    config.get_mode_settings = _mode_settings
    monkeypatch.setitem(sys.modules, "services.vanguard_config", config)

    sandbox = types.ModuleType("services.sandbox_runner")

    async def _run_check(probe, filename="vanguard_smoke.py", timeout=20):
        return {"ok": True}

    sandbox.run_python_check = _run_check
    monkeypatch.setitem(sys.modules, "services.sandbox_runner", sandbox)

    spec = importlib.util.spec_from_file_location(
        "services.vanguard_verify_agent",
        _SERVICES_DIR / "vanguard_verify_agent.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["services.vanguard_verify_agent"] = mod
    spec.loader.exec_module(mod)
    stubs = {"scanner": scanner, "llm": llm_pkg, "config": config,
             "sandbox": sandbox}
    return mod, stubs


@pytest.fixture()
def vva(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    mod, stubs = _load_vanguard(monkeypatch)
    return mod, stubs


def _set_model_response(stubs, raw: str):
    async def _fake(*args, **kwargs):
        return raw

    stubs["llm"].call_openrouter_model = _fake


def _set_scan(mod, monkeypatch, findings):
    # scan_file_blocks is bound into the module namespace at import
    # time (`from .vanguard_scanner import ...`), so patch the loaded
    # module's attribute — not the stub module's.
    monkeypatch.setattr(mod, "scan_file_blocks", lambda blocks: findings)


# ── (a) middle truncation in diff mode ───────────────────────────────

@pytest.mark.asyncio
async def test_diff_mode_scans_changed_middle_lines(vva, monkeypatch):
    """A vuln on changed line 400 of a 600-line file must be VISIBLE
    to the reviewer (old code sent only lines 1–250 + last 250)."""
    mod, stubs = vva
    seen = {}

    async def _capture(*args, **kwargs):
        seen["user"] = kwargs.get("user", "")
        return '{"pass": true, "findings": [], "summary": "ok"}'

    stubs["llm"].call_openrouter_model = _capture
    lines = [f"x = {i}" for i in range(1, 601)]
    lines[399] = "eval(user_input)  # changed"
    content = "\n".join(lines)
    base = "\n".join(f"x = {i}" for i in range(1, 601))
    await mod._llm_review({"a.py": content}, "repo",
                          line_map={"a.py": {400}})
    assert "eval(user_input)" in seen["user"]
    assert "400|" in seen["user"]
    assert "CHANGED_LINES: 400" in seen["user"]


@pytest.mark.asyncio
async def test_diff_mode_header_never_lists_truncated_lines(vva):
    """When the changed region is huge, the CHANGED_LINES header
    lists ONLY the changed lines actually present in the body."""
    mod, stubs = vva
    seen = {}

    async def _capture(*args, **kwargs):
        seen["user"] = kwargs.get("user", "")
        return '{"pass": true, "findings": [], "summary": "ok"}'

    stubs["llm"].call_openrouter_model = _capture
    content = "\n".join(f"y = {i}" for i in range(1, 701))
    await mod._llm_review({"a.py": content}, "repo",
                          line_map={"a.py": set(range(1, 701))})
    header = [ln for ln in seen["user"].splitlines()
              if ln.startswith("CHANGED_LINES:")][0]
    listed: set[int] = set()
    for part in header.split(":", 1)[1].split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            listed.update(range(int(a), int(b) + 1))
        else:
            listed.add(int(part))
    assert 401 in listed  # tail-kept changed line is listed
    assert 1 in listed  # head-kept changed line is listed
    assert 350 not in listed  # middle changed line was cut: NOT listed


# ── (b) fail-closed JSON parser ──────────────────────────────────────

@pytest.mark.asyncio
async def test_string_false_does_not_pass(vva):
    mod, stubs = vva
    _set_model_response(
        stubs,
        '{"pass": "false", "findings": [{"file": "a.py", "line": 1, '
        '"severity": "HIGH", "rule": "x", "message": "bad NoneType thing"}]}')
    res = await mod._llm_review({"a.py": "x = 1"}, "repo")
    assert res["pass"] is False
    # Scoped repair: the description survives verbatim (no None→null).
    assert res["findings"][0]["message"] == "bad NoneType thing"


@pytest.mark.asyncio
async def test_prose_with_no_json_fails_closed(vva):
    mod, stubs = vva
    _set_model_response(stubs, "I think the code looks fine overall, ship it.")
    res = await mod._llm_review({"a.py": "x = 1"}, "repo")
    assert res["pass"] is False
    assert any(f.get("rule") == "verify-agent-unparseable"
               for f in res["findings"])


@pytest.mark.asyncio
async def test_missing_pass_fails_closed(vva):
    mod, stubs = vva
    _set_model_response(stubs, '{"findings": [], "summary": "no verdict"}')
    res = await mod._llm_review({"a.py": "x = 1"}, "repo")
    assert res["pass"] is False
    assert any(f.get("rule") == "verify-agent-no-verdict"
               for f in res["findings"])
    # …and the block survives verify_patch's severity rule.
    res2 = await mod.verify_patch({"a.py": "x = 1"}, repo_ctx="r")
    assert res2["pass"] is False


@pytest.mark.asyncio
async def test_real_true_still_passes_and_python_literals_repaired(vva):
    mod, stubs = vva
    _set_model_response(
        stubs,
        '{"pass": True, "findings": [{"file": "a.py", "line": 1, '
        '"severity": "LOW", "rule": "x", '
        '"message": "returns NoneType here"}], "summary": "ok"}')
    res = await mod._llm_review({"a.py": "x = 1"}, "repo")
    assert res["pass"] is True
    assert res["findings"][0]["message"] == "returns NoneType here"


# ── (c) real import smoke test ───────────────────────────────────────

def _real_probe_runner():
    async def _run(probe, filename="vanguard_smoke.py", timeout=20):
        with tempfile.TemporaryDirectory() as td:
            proc = subprocess.run(
                [sys.executable, "-c", probe], cwd=td,
                capture_output=True, text=True, timeout=timeout)
            return {"ok": proc.returncode == 0,
                    "stderr": proc.stderr, "stdout": proc.stdout}
    return _run


@pytest.mark.asyncio
async def test_smoke_catches_bad_import(vva):
    mod, stubs = vva
    stubs["sandbox"].run_python_check = _real_probe_runner()
    res = await mod._e2b_smoke(
        {"bad.py": "import nonexistent_module_xyz\n\ndef f():\n    return 1\n"})
    assert res["pass"] is False


@pytest.mark.asyncio
async def test_smoke_catches_top_level_name_error(vva):
    mod, stubs = vva
    stubs["sandbox"].run_python_check = _real_probe_runner()
    res = await mod._e2b_smoke(
        {"bad.py": "undefined_name_xyz()\n\ndef f():\n    return 1\n"})
    assert res["pass"] is False


@pytest.mark.asyncio
async def test_smoke_passes_clean_module(vva):
    mod, stubs = vva
    stubs["sandbox"].run_python_check = _real_probe_runner()
    res = await mod._e2b_smoke(
        {"good.py": "import os\n\ndef f():\n    return os.path.sep\n"})
    assert res["pass"] is True


@pytest.mark.asyncio
async def test_smoke_probe_actually_imports(vva):
    """The generated probe must import (not compile) the module."""
    mod, stubs = vva
    seen = {}

    async def _capture(probe, filename="vanguard_smoke.py", timeout=20):
        seen["probe"] = probe
        return {"ok": True}

    stubs["sandbox"].run_python_check = _capture
    await mod._e2b_smoke({"m.py": "import os\n\ndef f():\n    return 1\n"})
    assert "importlib.import_module('m')" in seen["probe"]
    assert "compile(" not in seen["probe"]


# ── (d) crash containment + pass reconciliation ──────────────────────

@pytest.mark.asyncio
async def test_llm_task_crash_does_not_discard_regex_verdict(vva, monkeypatch):
    """A crashing LLM task used to raise out of verify_patch and lose
    the already-computed regex verdict (caller fell through to
    commit). Now it returns, fail-closed, with regex findings kept."""
    mod, stubs = vva
    _set_scan(mod, monkeypatch, [{"file": "a.py", "line": 1, "severity": "CRITICAL",
                       "rule": "eval", "message": "eval(user_input)"}])

    async def _boom(*args, **kwargs):
        raise asyncio.CancelledError("boom")

    monkeypatch.setattr(mod, "_llm_review", _boom)
    res = await mod.verify_patch({"a.py": "eval(user_input)"}, repo_ctx="r")
    assert res["pass"] is False
    assert res["regex"]["blocked"] is True
    assert res["regex"]["count"] == 1
    assert any(f.get("rule") == "verify-agent-crashed"
               for f in res["agent"]["findings"])


@pytest.mark.asyncio
async def test_e2b_task_crash_fails_closed(vva, monkeypatch):
    mod, stubs = vva
    _set_scan(mod, monkeypatch, [])

    async def _boom(*args, **kwargs):
        raise RuntimeError("sandbox exploded")

    monkeypatch.setattr(mod, "_e2b_smoke", _boom)
    res = await mod.verify_patch({"a.py": "x = 1"}, repo_ctx="r")
    assert res["pass"] is False
    assert res["e2b"]["pass"] is False


@pytest.mark.asyncio
async def test_pass_false_low_only_is_documented_rule_not_silent(vva):
    """pass=False + only LOW findings at CRITICAL block level: the
    documented severity rule passes the commit — but the divergence
    is now explicit (self_assessed_pass=False, agent.pass=True)."""
    mod, stubs = vva
    _set_model_response(
        stubs,
        '{"pass": false, "findings": [{"file": "a.py", "line": 1, '
        '"severity": "LOW", "rule": "x", "message": "smell"}], '
        '"summary": "low only"}')
    res = await mod.verify_patch({"a.py": "x = 1"}, repo_ctx="r")
    assert res["pass"] is True  # severity rule: LOW does not block
    assert res["agent"]["pass"] is True  # reported pass = blocking decision
    assert res["agent"]["self_assessed_pass"] is False
    assert "self-assessed" in res["summary"]


@pytest.mark.asyncio
async def test_pass_true_with_critical_finding_blocks(vva):
    """The other direction: reviewer said pass, but a CRITICAL
    finding blocks — agent.pass must reflect the block."""
    mod, stubs = vva
    _set_model_response(
        stubs,
        '{"pass": true, "findings": [{"file": "a.py", "line": 1, '
        '"severity": "CRITICAL", "rule": "x", "message": "real key"}], '
        '"summary": "critical found"}')
    res = await mod.verify_patch({"a.py": "x = 1"}, repo_ctx="r")
    assert res["pass"] is False
    assert res["agent"]["pass"] is False
    assert res["agent"]["self_assessed_pass"] is True
