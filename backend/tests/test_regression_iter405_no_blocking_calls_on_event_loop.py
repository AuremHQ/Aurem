"""Iter 405 — the app runs one uvicorn worker (one event loop), so a
bcrypt hash (~250ms) or a git/bash subprocess inside an `async def`
freezes every user's request for that long: the "wait, system is slow"
hiccup. These call sites must go through asyncio.to_thread."""
import ast
import asyncio
import pathlib
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

BCRYPT_FILES = [
    "routers/auth.py", "routers/oauth.py", "routers/ora_chat.py",
    "routers/team_management.py",
]
BLOCKING_HELPERS = {
    "services/ora_chat_v2/state_block.py": {"_git_build_hash"},
    "routers/feature_window.py": {"_wc", "_route_count"},
    "services/github_sync.py": {"_local_commits_behind"},
}


def _direct_calls_in_async(path, pred):
    tree = ast.parse((ROOT / path).read_text())
    hits = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        stack = list(ast.iter_child_nodes(fn))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # nested sync defs run only when handed to a thread
            if isinstance(n, ast.Call) and pred(n):
                hits.append((path, fn.name, n.lineno))
            stack.extend(ast.iter_child_nodes(n))
    return hits


@pytest.mark.parametrize("path", BCRYPT_FILES)
def test_regression_iter405_bcrypt_never_called_directly_in_async(path):
    def is_bcrypt(c):
        f = c.func
        return (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "bcrypt" and f.attr in ("hashpw", "checkpw"))
    assert _direct_calls_in_async(path, is_bcrypt) == []


@pytest.mark.parametrize("path", sorted(BLOCKING_HELPERS))
def test_regression_iter405_blocking_helpers_not_called_directly_in_async(path):
    names = BLOCKING_HELPERS[path]
    def is_helper(c):
        return isinstance(c.func, ast.Name) and c.func.id in names
    assert _direct_calls_in_async(path, is_helper) == []


def test_regression_iter405_build_hash_is_cached(monkeypatch):
    from services.ora_chat_v2 import state_block as sb
    calls = []
    monkeypatch.setattr(sb, "_git_build_hash_uncached",
                        lambda: calls.append(1) or "abc1234")
    monkeypatch.setattr(sb, "_build_hash_cache", None)
    assert sb._git_build_hash() == "abc1234"
    assert sb._git_build_hash() == "abc1234"
    assert len(calls) == 1


def test_regression_iter405_login_hashing_does_not_stall_the_loop():
    from services.password_hash import bcrypt_check as _bcrypt_check, bcrypt_hash as _bcrypt_hash

    async def run():
        ticks = []

        async def ticker():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        t = asyncio.create_task(ticker())
        await asyncio.sleep(0.03)
        h = await asyncio.to_thread(_bcrypt_hash, "pw-Correct1!")
        ok = await asyncio.to_thread(_bcrypt_check, "pw-Correct1!", h)
        bad = await asyncio.to_thread(_bcrypt_check, "nope", h)
        t.cancel()
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        return ok, bad, max(gaps)

    ok, bad, worst_gap = asyncio.run(run())
    assert ok is True and bad is False
    assert worst_gap < 0.15, f"event loop stalled {worst_gap:.3f}s during bcrypt"
