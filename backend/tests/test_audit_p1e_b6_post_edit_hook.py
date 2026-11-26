"""test_audit_p1e_b6_post_edit_hook.py — P1 audit fix B6 (2026-11-25).

B6: run_post_edit_hook() ran `yarn build` with a hardcoded
cwd="/app/frontend" — Aurem's OWN container checkout, not the user's
connected repo (all repo access is via the GitHub API; there is no
local checkout of the user's repo on this pod). That build validated
nothing about the user's edit. Now the hook resolves the project's
own frontend dir from the project/workspace path and SKIPS (never
builds) when no real frontend dir resolves.

Proved here by running the REAL run_post_edit_hook with a fake
create_subprocess_exec that records argv/cwd instead of spawning
yarn (no yarn binary exists on this VM). The fake's cwd is the
whole assertion: it must be the project's dir, never /app/frontend.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1e_stubs  # noqa: E402

p1e_stubs.install_p1e_stubs()

from services.orchestrator import (  # noqa: E402
    POST_EDIT_HOOKS,
    _resolve_frontend_dir,
    run_post_edit_hook,
)


# ── Fake subprocess ──────────────────────────────────────────────────
class FakeProc:
    def __init__(self, returncode=0, out=b"build ok\n"):
        self.returncode = returncode
        self._out = out

    async def communicate(self):
        return (self._out, b"")


def _install_fake_exec(monkeypatch, calls, returncode=0, out=b"build ok\n"):
    async def _fake(*argv, cwd=None, **kwargs):
        calls.append({"argv": argv, "cwd": cwd})
        return FakeProc(returncode=returncode, out=out)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)


def _mk_frontend(tmp_path, name="proj"):
    """A directory that counts as a frontend dir: has package.json."""
    root = tmp_path / name
    (root / "frontend").mkdir(parents=True)
    (root / "frontend" / "package.json").write_text('{"name": "x"}')
    return root


# ── The fix ──────────────────────────────────────────────────────────
def test_b6_no_frontend_dir_skips_and_never_spawns_yarn(monkeypatch, tmp_path):
    """No ctx path, no env, no local checkout of the user's repo:
    the hook must SKIP — it must NOT fall back to building
    /app/frontend (Aurem's own checkout)."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    monkeypatch.delenv("ORA_FRONTEND_DIR", raising=False)
    res = asyncio.run(run_post_edit_hook("frontend/src/App.jsx", {}))
    assert res == {"ok": True, "skipped": True,
                   "reason": "no_frontend_dir", "ext": "jsx"}
    assert calls == [], "yarn must never spawn when no frontend dir resolves"


def test_b6_explicit_frontend_dir_is_the_cwd(monkeypatch, tmp_path):
    """ctx["frontend_dir"] wins: yarn build runs THERE."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    fe = tmp_path / "my-frontend"
    fe.mkdir()
    (fe / "package.json").write_text('{"name": "x"}')
    res = asyncio.run(run_post_edit_hook(
        "src/App.jsx", {"frontend_dir": str(fe)}))
    assert res["ok"] is True
    assert res["frontend_dir"] == str(fe)
    assert len(calls) == 1
    assert calls[0]["argv"] == ("yarn", "build")
    assert calls[0]["cwd"] == str(fe)


def test_b6_project_path_frontend_subdir_is_the_cwd(monkeypatch, tmp_path):
    """ctx["project_path"] + <root>/frontend/package.json."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    root = _mk_frontend(tmp_path)
    res = asyncio.run(run_post_edit_hook(
        "frontend/src/App.jsx", {"project_path": str(root)}))
    assert res["ok"] is True
    assert res["frontend_dir"] == str(root / "frontend")
    assert calls[0]["cwd"] == str(root / "frontend")


def test_b6_project_path_first_segment_derivation(monkeypatch, tmp_path):
    """Edited file under web/src/... + <root>/web/package.json:
    the first path segment resolves the frontend dir."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    root = tmp_path / "proj"
    (root / "web").mkdir(parents=True)
    (root / "web" / "package.json").write_text('{"name": "x"}')
    res = asyncio.run(run_post_edit_hook(
        "web/src/App.jsx", {"workspace_path": str(root)}))
    assert res["ok"] is True
    assert res["frontend_dir"] == str(root / "web")
    assert calls[0]["cwd"] == str(root / "web")


def test_b6_project_root_itself_can_be_the_frontend(monkeypatch, tmp_path):
    """Repo-root-is-frontend layout: package.json at the root."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "package.json").write_text('{"name": "x"}')
    res = asyncio.run(run_post_edit_hook(
        "src/App.jsx", {"project_path": str(root)}))
    assert res["ok"] is True
    assert res["frontend_dir"] == str(root)


def test_b6_frontend_dir_without_package_json_is_not_a_frontend(
        monkeypatch, tmp_path):
    """A directory without package.json is not a frontend dir:
    skip, do not build there."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    empty = tmp_path / "not-frontend"
    empty.mkdir()
    res = asyncio.run(run_post_edit_hook(
        "src/App.jsx", {"frontend_dir": str(empty)}))
    assert res["skipped"] is True
    assert res["reason"] == "no_frontend_dir"
    assert calls == []


def test_b6_nonexistent_frontend_dir_skips(monkeypatch):
    """A configured-but-missing dir skips instead of crashing
    create_subprocess_exec with a bad cwd."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    res = asyncio.run(run_post_edit_hook(
        "src/App.jsx", {"frontend_dir": "/nonexistent/nope"}))
    assert res["skipped"] is True
    assert res["reason"] == "no_frontend_dir"
    assert calls == []


def test_b6_env_override_used_when_no_ctx_path(monkeypatch, tmp_path):
    """ORA_FRONTEND_DIR is the last-resort override for
    single-project deployments."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    fe = tmp_path / "env-frontend"
    fe.mkdir()
    (fe / "package.json").write_text('{"name": "x"}')
    monkeypatch.setenv("ORA_FRONTEND_DIR", str(fe))
    res = asyncio.run(run_post_edit_hook("src/App.jsx", {}))
    assert res["ok"] is True
    assert calls[0]["cwd"] == str(fe)


def test_b6_ctx_beats_env(monkeypatch, tmp_path):
    """An explicit ctx frontend dir beats the env override."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    fe_ctx = tmp_path / "ctx-fe"
    fe_ctx.mkdir()
    (fe_ctx / "package.json").write_text('{"name": "x"}')
    fe_env = tmp_path / "env-fe"
    fe_env.mkdir()
    (fe_env / "package.json").write_text('{"name": "x"}')
    monkeypatch.setenv("ORA_FRONTEND_DIR", str(fe_env))
    res = asyncio.run(run_post_edit_hook(
        "src/App.jsx", {"frontend_dir": str(fe_ctx)}))
    assert calls[0]["cwd"] == str(fe_ctx)


def test_b6_py_hook_untouched(monkeypatch):
    """The .py branch (py_compile on the file) is out of scope for
    B6 and must behave exactly as before."""
    calls = []
    _install_fake_exec(monkeypatch, calls)
    res = asyncio.run(run_post_edit_hook("backend/main.py", {}))
    assert res["ok"] is True
    assert res["ext"] == "py"
    assert calls[0]["argv"][:3] == ("python3", "-m", "py_compile")
    assert calls[0]["cwd"] is None


def test_b6_failed_build_still_raises_signal(monkeypatch, tmp_path):
    """A failing build in the PROJECT's dir still raises the
    build_check_failed system signal (the signal behaviour is
    unchanged — only the directory it builds is fixed)."""
    calls = []
    _install_fake_exec(monkeypatch, calls, returncode=1,
                       out=b"ERROR: build failed\n")
    fe = tmp_path / "fe"
    fe.mkdir()
    (fe / "package.json").write_text('{"name": "x"}')
    ctx: dict = {"frontend_dir": str(fe)}
    res = asyncio.run(run_post_edit_hook("src/App.jsx", ctx))
    assert res["ok"] is False
    assert "build_check_failed" in ctx["system_signals"]


# ── Source guards (the hardcoded path must be gone from the hook) ────
def test_b6_no_hardcoded_app_frontend_in_hook_or_allowlist():
    """No live "/app/frontend" remains in run_post_edit_hook or in
    the POST_EDIT_HOOKS values (they are allowlist-only strings)."""
    src = inspect.getsource(run_post_edit_hook)
    assert "/app/frontend" not in src
    for ext, cmd in POST_EDIT_HOOKS.items():
        assert "/app/frontend" not in cmd, ext
    assert POST_EDIT_HOOKS["jsx"] == "yarn build"
    assert POST_EDIT_HOOKS["js"] == "yarn build"
    assert POST_EDIT_HOOKS["tsx"] == "yarn build"


def test_b6_resolver_rejects_relative_and_dot_segments():
    """_resolve_frontend_dir never resolves a relative path, and a
    '..' first segment cannot escape the project root."""
    assert _resolve_frontend_dir("src/App.jsx", {}) is None
    assert _resolve_frontend_dir("../etc/App.jsx",
                                 {"project_path": "/tmp"}) is None
    assert _resolve_frontend_dir("src/App.jsx",
                                 {"frontend_dir": "relative/path"}) is None
