"""Part XIV (a4) — MCP write path must go through the Part-C scope gate
(backend/routers/mcp.py).

Loads the REAL local_tools module (same stub set as
test_part14_localtools.py) for its real _check_write_scope, then loads
the REAL mcp router with stubbed fastapi/httpx/cto_services, and a fake
services.local_tools whose invoke_local_tool applies the real scope
gate. Backend root resolves from PART14_BACKEND (default: this file's
parents[1]) so the same file runs against the fixed tree AND against
pristine as a negative control.
"""
import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(os.environ.get(
    "PART14_BACKEND", Path(__file__).resolve().parents[1]))
SERVICES = BACKEND / "services"


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    # Delegate any other attribute to the REAL module so `from <name>
    # import <other>` keeps working for every other test file
    # collected after this one (same fix as tests/p1e_stubs.py).
    # The old bare fake for services.vanguard_scanner (no
    # DANGEROUS_PATTERNS) and core.errors (no BinaryFileError) broke
    # those imports for later files. Defined attributes win, so the
    # MCP tests still see the fakes.
    _real = [None]

    def __getattr__(attr, _name=name, _real=_real):
        if _real[0] is None:
            import importlib
            saved = sys.modules.pop(_name, None)
            try:
                _real[0] = importlib.import_module(_name)
            finally:
                if saved is not None:
                    sys.modules[_name] = saved
        return getattr(_real[0], attr)

    m.__getattr__ = __getattr__
    _save(name)
    sys.modules[name] = m
    return m


def _pkg(name, path=None):
    m = types.ModuleType(name)
    m.__path__ = [str(path)] if path else []
    _save(name)
    sys.modules[name] = m
    return m


# Names this file stubs, with whatever was in sys.modules before (or a
# sentinel when the name was absent). The stubs exist ONLY so the REAL
# routers/mcp.py can be imported in a sandbox without fastapi/httpx
# installed; once it is loaded, the stubs must be removed again, or
# they shadow the REAL modules for every other test file in the same
# pytest process (the 27 fastapi + 10 httpx collection ImportErrors and
# the ~9.5k conftest-fixture setup errors in the full suite all trace
# to module-level stubs like these). The loaded router keeps its own
# bound references, so removing the stubs does not change what these
# tests observe. `services.local_tools` (= the REAL module, loaded
# below) and `part14_mcp_router` are deliberately NOT restored — the
# tests below address them through sys.modules.
_SAVED = {}
_MISSING = object()


def _save(name):
    if name not in _SAVED:
        _SAVED[name] = sys.modules.get(name, _MISSING)


def _restore_stubs():
    """Remove the import-time stubs that shadow REAL modules for other
    test files — but ONLY the ones this file's tests do not depend on
    through the router's lazy imports.

    Restored (the mass-error names): the ``fastapi`` / ``httpx`` /
    ``cto_services`` fakes and the ``services`` / ``cto_services`` /
    ``core`` package fakes. The router bound those at load time, and
    no test here re-imports them, so restoring the real modules is
    invisible to these tests.

    Kept (do NOT restore): the ``services.*`` submodule fakes
    (``local_tools``, ``mcp_scoped_tools``, ``repo_context``,
    ``vanguard_scanner``, …) and ``core.errors``. The router lazily
    imports those inside its endpoint functions, and the tests depend
    on the fake behaviours (e.g. ``mcp_scoped_tools``'s no-op
    ``update_session_from_call`` — the real one requires a selected
    project and fails these scope tests). No other test file imports
    those submodule names at module level in a way that fails, so
    keeping them is harmless to the rest of the suite.
    """
    _KEEP_PREFIXES = ("services.",)
    _KEEP_EXACT = {"core.errors"}
    for name, prior in list(_SAVED.items()):
        if name in _KEEP_EXACT or name.startswith(_KEEP_PREFIXES):
            continue
        if prior is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prior
        del _SAVED[name]


# --- load REAL local_tools (for the real _check_write_scope) -----------
_pkg("services", SERVICES)
# Real __path__ (not []) so submodule imports resolve to the real
# files AND the _mod __getattr__ delegation can load the real
# submodule. An empty __path__ made `importlib.import_module` fail
# with ModuleNotFoundError when a fake submodule delegated to real.
_pkg("cto_services", BACKEND / "cto_services")
_pkg("core", BACKEND / "core")
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
    return {"ok": True, "tool": tool_name, "data": await runner(*a, **k)}


_mod("services.tool_executor", execute=_tx_execute)
_mod("services.vanguard_scanner", scan_file_blocks=lambda files: [],
     has_critical_or_high=lambda findings: False)
_mod("services.loop_diff_classifier", is_test_or_fixture=lambda p: False)


try:
    # Use the REAL WriteGuardBlockedError so the router raises the same
    # class the guardrail tests (tests/guardrails/test_path_guard.py)
    # expect. Defining a new class here made the router raise a
    # different class than core.errors.WriteGuardBlockedError, breaking
    # the guardrails. Fall back to a local class only if the real
    # module cannot be imported (stripped tree).
    from core.errors import WriteGuardBlockedError as _WriteGuardBlockedError
except Exception:  # noqa: BLE001 — stripped tree, use a local stand-in
    class _WriteGuardBlockedError(Exception):
        pass


_mod("core.errors", WriteGuardBlockedError=_WriteGuardBlockedError)
_mod("services.write_guard",
     check_write_paths=lambda *a, **k: asyncio.sleep(0))
_mod("services.github_cache", invalidate_repo=lambda *a, **k: None)
_mod("services.contrast_guard",
     check_and_nudge_css=lambda c: {"adjustments": [], "content": c},
     describe_nudge=lambda adj: "")
# NOTE: services.github_api_writer / services.git_identity are NOT
# stubbed here — the MCP tests never run the real write_repo_file
# (the fake invoke_local_tool stands in), and stubbing them would
# clobber test_part14_localtools.py's recording commit stub when
# both files share one pytest process (collection order).

_spec = importlib.util.spec_from_file_location(
    "services.local_tools", str(SERVICES / "local_tools.py"))
real_lt = importlib.util.module_from_spec(_spec)
sys.modules["services.local_tools"] = real_lt
_spec.loader.exec_module(real_lt)
REAL_CHECK_SCOPE = real_lt._check_write_scope

# --- fake services.local_tools for the MCP router -----------------------
# invoke_local_tool applies the REAL scope gate, then pretends the
# write succeeded — this tests the MCP wiring (ctx carries session
# scope), not the GitHub commit itself.


async def _fake_invoke_local_tool(name, args, ctx):
    blocked = REAL_CHECK_SCOPE(name, args, ctx)
    if blocked is not None:
        return blocked
    return {"ok": True, "sha": "deadbeef",
            "html_url": "https://example/x"}


async def _fake_read_repo_file(ctx, args):
    return {"ok": True, "content": "print(1)\n", "size": 9,
            "sha": "abc", "encoding": "utf-8"}


async def _fake_list_repo_files(ctx, args):
    return {"ok": True, "tree": ["a.py", "b.py"], "total": 2,
            "truncated": False}


async def _fake_search_repo(ctx, args):
    return {"ok": True,
            "matches": [{"file": "a.py", "line_no": 1, "line": "x"}],
            "total_matches": 1, "complete": True}


_mod("services.local_tools",
     invoke_local_tool=_fake_invoke_local_tool,
     read_repo_file=_fake_read_repo_file,
     list_repo_files=_fake_list_repo_files,
     search_repo=_fake_search_repo)

# Delegate any other attribute to the REAL local_tools (loaded above
# as real_lt) so `from services.local_tools import
# _apply_chunking/write_repo_file/_snapshot_cache_key/...` keeps
# working for every other test file collected after this one — the
# bare fake broke those imports. Defined attributes win, so the MCP
# tests still see the fakes above.
sys.modules["services.local_tools"].__getattr__ = (
    lambda attr: getattr(real_lt, attr))


async def _noop_update_session(session_key, name, args):
    return None


_mod("services.mcp_scoped_tools",
     update_session_from_call=_noop_update_session,
     sanitize_for_llm=lambda s: s)

# --- stub fastapi / httpx / cto_services for routers/mcp.py -------------


class _FakeHTTPException(Exception):
    def __init__(self, status_code=500, detail=""):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _FakeRequest:
    pass


class _FakeJSONResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


class _FakeRouter:
    def __init__(self, *a, **k):
        pass

    def _deco(self, *a, **k):
        def wrap(fn):
            return fn
        return wrap

    get = _deco
    post = _deco
    delete = _deco
    put = _deco
    patch = _deco


def _fake_header(default=None, **k):
    return default


_mod("fastapi", APIRouter=_FakeRouter, Header=_fake_header,
     HTTPException=_FakeHTTPException, Request=_FakeRequest,
     JSONResponse=_FakeJSONResponse)
_mod("fastapi.responses", JSONResponse=_FakeJSONResponse)
_mod("httpx", AsyncClient=object)
_mod("cto_services.auth", current_dev=lambda *a, **k: None)

_spec = importlib.util.spec_from_file_location(
    "part14_mcp_router", str(BACKEND / "routers" / "mcp.py"))
mcp = importlib.util.module_from_spec(_spec)
sys.modules["part14_mcp_router"] = mcp
_spec.loader.exec_module(mcp)

# The router is loaded; remove the import-time stubs so the real
# fastapi / httpx / cto_services / services modules are visible to
# every other test file in this pytest process again (see _SAVED).
_restore_stubs()


def _run(coro):
    return asyncio.run(coro)


async def _fake_resolve_user(authorization):
    return {"user_id": "u1"}


async def _fake_mcp_ctx(user_id, project_id):
    return {"user_id": user_id, "project_id": project_id,
            "bin_ctx": object()}


def _setup(monkeypatch):
    monkeypatch.setattr(mcp, "_resolve_user", _fake_resolve_user)
    monkeypatch.setattr(mcp, "_mcp_ctx_for", _fake_mcp_ctx)
    mcp._MCP_WRITE_SCOPE.clear()


def _call(session, name, args):
    return _run(mcp._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": args}},
        "Bearer sk-aurem-test", session))


def _is_rpc_error(result):
    return "error" in (result or {})


# --- scope store -------------------------------------------------------------
def test_session_scope_store_isolated_per_session(monkeypatch):
    _setup(monkeypatch)
    tok = mcp._MCP_SESSION_KEY.set("sess-A")
    try:
        mcp._mcp_note_discovered(["a.py"])
        assert mcp._mcp_session_scope() == {"a.py"}
    finally:
        mcp._MCP_SESSION_KEY.reset(tok)
    tok = mcp._MCP_SESSION_KEY.set("sess-B")
    try:
        assert mcp._mcp_session_scope() == set()
    finally:
        mcp._MCP_SESSION_KEY.reset(tok)
    # No session key set: recording is a no-op (fail-closed default).
    mcp._mcp_note_discovered(["z.py"])
    assert mcp._MCP_WRITE_SCOPE == {"sess-A": {"a.py"}}


# --- full dispatcher path ------------------------------------------------------
def test_mcp_write_without_prior_read_is_denied(monkeypatch):
    _setup(monkeypatch)
    r = _call("sess-1", "write_repo_file",
              {"project_id": "p1", "file_path": "foo.py",
               "content": "x = 1\n"})
    assert _is_rpc_error(r), r
    assert "scope" in r["error"]["message"].lower(), r


def test_mcp_write_after_read_passes_same_session(monkeypatch):
    _setup(monkeypatch)
    r = _call("sess-1", "read_repo_file",
              {"project_id": "p1", "file_path": "foo.py"})
    assert not _is_rpc_error(r), r
    r = _call("sess-1", "write_repo_file",
              {"project_id": "p1", "file_path": "foo.py",
               "content": "x = 2\n"})
    assert not _is_rpc_error(r), r
    assert r["result"]["data"]["commit_sha"] == "deadbeef", r


def test_mcp_write_to_unread_file_denied_after_a_read(monkeypatch):
    _setup(monkeypatch)
    r = _call("sess-1", "read_repo_file",
              {"project_id": "p1", "file_path": "foo.py"})
    assert not _is_rpc_error(r), r
    r = _call("sess-1", "write_repo_file",
              {"project_id": "p1", "file_path": "other.py",
               "content": "x = 2\n"})
    assert _is_rpc_error(r), r
    assert "scope" in r["error"]["message"].lower(), r


def test_mcp_scope_does_not_leak_across_sessions(monkeypatch):
    _setup(monkeypatch)
    r = _call("sess-A", "read_repo_file",
              {"project_id": "p1", "file_path": "foo.py"})
    assert not _is_rpc_error(r), r
    r = _call("sess-B", "write_repo_file",
              {"project_id": "p1", "file_path": "foo.py",
               "content": "x = 2\n"})
    assert _is_rpc_error(r), r


def test_mcp_list_and_search_seed_scope(monkeypatch):
    _setup(monkeypatch)
    r = _call("sess-1", "list_repo_files",
              {"project_id": "p1", "path": ""})
    assert not _is_rpc_error(r), r
    r = _call("sess-1", "write_repo_file",
              {"project_id": "p1", "file_path": "b.py",
               "content": "x = 2\n"})
    assert not _is_rpc_error(r), r
    r = _call("sess-2", "search_repo",
              {"project_id": "p1", "query": "print"})
    assert not _is_rpc_error(r), r
    r = _call("sess-2", "write_repo_file",
              {"project_id": "p1", "file_path": "a.py",
               "content": "x = 2\n"})
    assert not _is_rpc_error(r), r


def test_mcp_write_direct_without_dispatcher_is_denied(monkeypatch):
    # No _handle_one call means no session key: fail-closed default.
    _setup(monkeypatch)
    monkeypatch.setattr(mcp, "_mcp_ctx_for", _fake_mcp_ctx)
    with pytest.raises(RuntimeError, match="scope"):
        _run(mcp._tool_write_repo_file(
            "u1", {"project_id": "p1", "file_path": "foo.py",
                   "content": "x = 1\n"}))


def test_mcp_failed_read_does_not_seed_scope(monkeypatch):
    _setup(monkeypatch)

    async def _failing_read(ctx, args):
        return {"ok": False, "error": "not found"}

    monkeypatch.setattr(sys.modules["services.local_tools"],
                        "read_repo_file", _failing_read)
    with pytest.raises(RuntimeError):
        _run(mcp._tool_read_repo_file(
            "u1", {"project_id": "p1", "file_path": "foo.py"}))
    tok = mcp._MCP_SESSION_KEY.set("sess-9")
    try:
        assert mcp._mcp_session_scope() == set()
    finally:
        mcp._MCP_SESSION_KEY.reset(tok)
