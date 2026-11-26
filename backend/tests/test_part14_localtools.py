"""Part XIV — local-tools guard fixes (backend/services/local_tools.py):
(a1/a3) empty-scope fail-closed, (a2) scope-path normalization,
(b1/b2) syntax gate fail-closed on broken tooling + skipped-flagging,
(c1/c2) _is_safe_repo_path traversal/absolute rejection.

Loads the REAL local_tools module with stubbed sibling imports (the
module needs DB/GitHub imports at module level). Backend root resolves
from PART14_BACKEND (default: this file's parents[1]) so the same file
runs against the fixed tree AND against pristine as a negative control.
"""
import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(os.environ.get(
    "PART14_BACKEND", Path(__file__).resolve().parents[1]))
SERVICES = BACKEND / "services"


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def _pkg(name, path=None):
    m = types.ModuleType(name)
    m.__path__ = [str(path)] if path else []
    sys.modules[name] = m
    return m


# --- stub packages / sibling imports BEFORE loading local_tools --------
_pkg("services", SERVICES)
_pkg("cto_services")
_pkg("core")
_mod("cto_services.db", get_db=lambda: None)


class _GithubAuthError(Exception):
    pass


async def _fake_fetch_file(*a, **k):
    return None


_mod("services.repo_context", _fetch_file=_fake_fetch_file,
     GithubAuthError=_GithubAuthError)
_mod("services.web_skills", WEB_TOOLS={}, WEB_TOOL_SPECS=[])


async def _dummy_browser(*a, **k):
    return {"ok": True}


_mod("services.browser_tools", browser=_dummy_browser)
_mod("services.dev_skills", DEV_TOOLS={}, DEV_TOOL_SPECS=[])
_mod("services.vercel_skills", VERCEL_TOOLS={}, VERCEL_TOOL_SPECS=[])
_mod("services.context7_skills", CONTEXT7_TOOLS={}, CONTEXT7_TOOL_SPECS=[])
_mod("services.sequential_thinking_skills",
     SEQUENTIAL_THINKING_TOOLS={},
     SEQUENTIAL_THINKING_TOOL_SPECS=[])
_mod("services.interaction_contract", enabled=lambda: False,
     read_only_request=lambda prompt: False)
_mod("services.task_chain_logging", redact_secrets=lambda s: s)


async def _tx_execute(tool_name, runner, *a, **k):
    try:
        result = await runner(*a, **k)
    except Exception as e:  # noqa: BLE001 - passthrough test stub
        return {"ok": False, "tool": tool_name, "system_signal": "unknown_error",
                "severity": "error", "error_class": type(e).__name__,
                "error_message": str(e)[:240],
                "llm_facing": f"Tool {tool_name} could not complete."}
    return {"ok": True, "tool": tool_name, "data": result}


_mod("services.tool_executor", execute=_tx_execute)
_mod("services.vanguard_scanner", scan_file_blocks=lambda files: [],
     has_critical_or_high=lambda findings: False)
_mod("services.loop_diff_classifier", is_test_or_fixture=lambda p: False)


class _WriteGuardBlockedError(Exception):
    pass


_mod("core.errors", WriteGuardBlockedError=_WriteGuardBlockedError)


async def _noop_check_write_paths(*a, **k):
    return None


_mod("services.write_guard", check_write_paths=_noop_check_write_paths)
_mod("services.github_cache", invalidate_repo=lambda *a, **k: None)
_mod("services.contrast_guard",
     check_and_nudge_css=lambda c: {"adjustments": [], "content": c},
     describe_nudge=lambda adj: "")

COMMIT_CALLS = []


async def _fake_commit_files(*a, **k):
    COMMIT_CALLS.append((a, k))
    return {"sha": "deadbeef", "html_url": "https://example/x"}


_mod("services.github_api_writer", commit_files=_fake_commit_files)


async def _fake_identity(db, user_id):
    return ("A U Thor", "a@example.com")


_mod("services.git_identity", resolve_git_identity=_fake_identity,
     build_commit_message=lambda **k: k.get("user_message") or "x")

_spec = importlib.util.spec_from_file_location(
    "services.local_tools", str(SERVICES / "local_tools.py"))
lt = importlib.util.module_from_spec(_spec)
sys.modules["services.local_tools"] = lt
_spec.loader.exec_module(lt)


def _run(coro):
    return asyncio.run(coro)


# Autouse: re-install this file's recording commit stub before every
# test — test_part14_mcp.py shares this pytest process and registers
# its own sibling stubs at collection time; the commit recorder must
# be the one these tests assert on.
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _recording_commit_stub():
    sys.modules["services.github_api_writer"] = types.ModuleType(
        "services.github_api_writer")
    sys.modules["services.github_api_writer"].commit_files = \
        _fake_commit_files
    yield


def _bin_ctx():
    return SimpleNamespace(bin_id="u1", repo_owner="o", repo_name="r",
                           branch="main", pat="tok", is_founder=False,
                           pid="p1")


def _ctx(**extra):
    ctx = {"user_id": "u1", "project_id": "p1", "bin_ctx": _bin_ctx()}
    ctx.update(extra)
    return ctx


# --- (c1/c2) _is_safe_repo_path --------------------------------------------
def test_safe_path_rejects_traversal_and_absolute():
    assert lt._is_safe_repo_path("../x") is False
    assert lt._is_safe_repo_path("/etc/x") is False
    assert lt._is_safe_repo_path("a/../../etc/passwd") is False
    assert lt._is_safe_repo_path("a/../b.py") is False


def test_safe_path_still_accepts_benign_and_blocks_charset():
    assert lt._is_safe_repo_path("foo.py") is True
    assert lt._is_safe_repo_path("a/b/c.py") is True
    assert lt._is_safe_repo_path("foo..py") is True  # ".." as substring
    assert lt._is_safe_repo_path("a b.py") is True
    assert lt._is_safe_repo_path("foo;rm -rf") is False
    assert lt._is_safe_repo_path("$(id)") is False
    assert lt._is_safe_repo_path("") is False
    assert lt._is_safe_repo_path(None) is False


# --- (a2) normalization -------------------------------------------------------
def test_normalize_scope_path():
    assert lt._normalize_scope_path("./foo.py") == "foo.py"
    assert lt._normalize_scope_path("src//foo.py") == "src/foo.py"
    assert lt._normalize_scope_path("src/./foo.py") == "src/foo.py"
    assert lt._normalize_scope_path("foo.py") == "foo.py"
    assert lt._normalize_scope_path("/foo.py") == "foo.py"
    assert lt._normalize_scope_path("") == ""


def test_scope_check_normalized_match_both_ways():
    ctx = {"scope_files": {"foo.py"}}
    assert lt._check_write_scope(
        "write_repo_file", {"path": "./foo.py"}, ctx) is None
    ctx = {"scope_files": {"./foo.py"}}  # prompt spelling
    assert lt._check_write_scope(
        "write_repo_file", {"path": "foo.py"}, ctx) is None
    ctx = {"scope_files": {"src/foo.py"}}
    assert lt._check_write_scope(
        "write_repo_file", {"path": "src//foo.py"}, ctx) is None
    # Still blocks genuinely different files.
    assert lt._check_write_scope(
        "write_repo_file", {"path": "src/other.py"}, ctx) is not None


def test_record_discovered_scope_stores_canonical():
    ctx = {}
    lt._record_discovered_scope("read_repo_file", {"path": "./foo.py"},
                                {"ok": True}, ctx)
    assert ctx["scope_files"] == {"foo.py"}, ctx


# --- (a1/a3) empty scope fails closed -------------------------------------------
def test_empty_scope_write_is_blocked():
    for ctx in ({}, {"user_prompt": "fix it"},
                {"user_prompt": "", "scope_files": set()}):
        r = lt._check_write_scope(
            "write_repo_file", {"path": "foo.py"}, ctx)
        assert r is not None and r.get("error_class") == "scope_blocked", \
            (ctx, r)
        assert r.get("system_signal") == "scope_blocked"


def test_write_to_prompt_named_file_still_passes_first_call():
    ctx = {"user_prompt": "fix the bug in foo.py please"}
    assert lt._check_write_scope(
        "write_repo_file", {"path": "foo.py"}, ctx) is None
    r = lt._check_write_scope(
        "write_repo_file", {"path": "other.py"}, ctx)
    assert r is not None and r["error_class"] == "scope_blocked"


def test_write_to_task_state_seeded_file_still_passes():
    ctx = {"scope_files": {"x.py"}}  # orchestrator task_state pre-seed
    assert lt._check_write_scope(
        "write_repo_file", {"path": "x.py"}, ctx) is None
    ctx = {"scope_files": {"x.py"}}
    r = lt._check_write_scope(
        "write_repo_file", {"path": "y.py"}, ctx)
    assert r is not None and r["error_class"] == "scope_blocked"


def test_check_write_scope_no_path_is_not_a_block():
    assert lt._check_write_scope("write_repo_file", {}, {}) is None


def test_invoke_local_tool_enforces_empty_scope(monkeypatch):
    async def _fake_write(ctx, args):
        return {"ok": True, "sha": "x"}

    monkeypatch.setitem(lt.LOCAL_TOOLS, "write_repo_file", _fake_write)
    r = _run(lt.invoke_local_tool(
        "write_repo_file", {"path": "foo.py"}, _ctx()))
    assert r["ok"] is False and r["error_class"] == "scope_blocked", r
    r = _run(lt.invoke_local_tool(
        "write_repo_file", {"path": "foo.py"},
        _ctx(user_prompt="please fix foo.py")))
    assert r["ok"] is True and r["sha"] == "x", r


# --- (b1/b2) syntax gate ---------------------------------------------------------
def _write_py(content, monkeypatch, gate_stub=None):
    if gate_stub is not None:
        monkeypatch.setattr(lt, "_run_syntax_check", gate_stub)
    COMMIT_CALLS.clear()
    ctx = _ctx(user_prompt="fix foo.py")
    return _run(lt.write_repo_file(
        ctx, {"path": "foo.py", "content": content})), ctx


def test_syntax_gate_passes_and_records_passed(monkeypatch):
    # NOTE: the real _run_syntax_check shells out to `python`, which
    # does not exist on this VM (only python3) — so the passed branch
    # is driven with a stubbed gate returning "passed", and the real
    # binary path is left to CI/prod. The stub still exercises the
    # real write_repo_file wiring + result field.
    def _stub(*, content, file_path, ext):
        return {"has_errors": False, "errors": "", "skipped": False}

    r, _ = _write_py("x = 1\n", monkeypatch, _stub)
    assert r["ok"] is True, r
    assert r["syntax_gate"] == {"status": "passed"}, r
    assert len(COMMIT_CALLS) == 1


def test_syntax_gate_real_syntax_error_still_blocks(monkeypatch):
    # Same VM caveat as the passed test: the real py_compile binary
    # is absent here, so a real syntax error is simulated with a
    # stubbed gate returning has_errors (the has_errors branch is
    # pre-existing behaviour, unchanged by this lane).
    def _stub(*, content, file_path, ext):
        return {"has_errors": True, "errors": "SyntaxError: bad",
                "skipped": False}

    r, _ = _write_py("def broken(:\n", monkeypatch, _stub)
    assert r["ok"] is False and r["error"] == "syntax_gate_blocked", r
    assert r["syntax_errors"] == "SyntaxError: bad", r
    assert len(COMMIT_CALLS) == 0


def test_syntax_gate_missing_binary_blocks(monkeypatch):
    def _stub(*, content, file_path, ext):
        return {"has_errors": False, "errors": "",
                "skipped": True, "reason": "py_check:FileNotFoundError"}

    r, ctx = _write_py("x = 1\n", monkeypatch, _stub)
    assert r["ok"] is False, r
    assert r["error"] == "syntax_gate_blocked", r
    assert r["gate"] == "syntax_gate", r
    assert r["syntax_gate"] == {"status": "skipped",
                                "reason": "py_check:FileNotFoundError"}, r
    assert len(COMMIT_CALLS) == 0  # nothing committed
    assert {"path": "foo.py",
            "reason": "py_check:FileNotFoundError"} in \
        ctx.get("syntax_gate_skipped", [])


def test_syntax_gate_tmp_write_failure_blocks(monkeypatch):
    def _stub(*, content, file_path, ext):
        return {"has_errors": False, "errors": "",
                "skipped": True, "reason": "tmp_write:OSError"}

    r, _ = _write_py("x = 1\n", monkeypatch, _stub)
    assert r["ok"] is False and r["error"] == "syntax_gate_blocked", r
    assert len(COMMIT_CALLS) == 0


def test_syntax_gate_timeout_fails_open_but_is_flagged(monkeypatch):
    def _stub(*, content, file_path, ext):
        return {"has_errors": False, "errors": "",
                "skipped": True, "reason": "py_check:TimeoutExpired"}

    r, ctx = _write_py("x = 1\n", monkeypatch, _stub)
    assert r["ok"] is True, r  # timeout still ships (flake, not defect)
    assert r["syntax_gate"] == {"status": "skipped",
                                "reason": "py_check:TimeoutExpired"}, r
    assert {"path": "foo.py",
            "reason": "py_check:TimeoutExpired"} in \
        ctx.get("syntax_gate_skipped", [])
    assert len(COMMIT_CALLS) == 1


def test_syntax_gate_ts_type_only_errors_ship_but_flagged(monkeypatch):
    def _stub(*, content, file_path, ext):
        return {"has_errors": False, "errors": "",
                "skipped": True, "reason": "ts_only_type_errors"}

    monkeypatch.setattr(lt, "_run_syntax_check", _stub)
    COMMIT_CALLS.clear()
    ctx = _ctx(user_prompt="fix foo.ts")
    r = _run(lt.write_repo_file(
        ctx, {"path": "foo.ts", "content": "const x: number = 1;\n"}))
    assert r["ok"] is True, r
    assert r["syntax_gate"] == {"status": "skipped",
                                "reason": "ts_only_type_errors"}, r
    assert len(COMMIT_CALLS) == 1


def test_syntax_gate_explicit_override_allows_broken_tooling(monkeypatch):
    def _stub(*, content, file_path, ext):
        return {"has_errors": False, "errors": "",
                "skipped": True, "reason": "py_check:FileNotFoundError"}

    monkeypatch.setattr(lt, "_run_syntax_check", _stub)
    COMMIT_CALLS.clear()
    ctx = _ctx(user_prompt="fix foo.py")
    r = _run(lt.write_repo_file(ctx, {
        "path": "foo.py", "content": "x = 1\n",
        "allow_unverified_syntax": True}))
    assert r["ok"] is True, r
    assert r["syntax_gate"]["status"] == "skipped", r
    assert len(COMMIT_CALLS) == 1


def test_syntax_gate_not_run_for_ungated_extension(monkeypatch):
    COMMIT_CALLS.clear()
    ctx = _ctx(user_prompt="fix foo.md")
    r = _run(lt.write_repo_file(
        ctx, {"path": "foo.md", "content": "# hi\n"}))
    assert r["ok"] is True, r
    assert "syntax_gate" not in r, r
