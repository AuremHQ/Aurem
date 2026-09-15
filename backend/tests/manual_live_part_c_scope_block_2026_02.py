"""
Live, real-tool-call reproduction of Part C's scope-gate BLOCK path.
Run manually (not part of pytest collection) against the actual
running backend's environment/DB/GitHub connection, using the REAL
(unmocked) services.local_tools.invoke_local_tool, services.local_tools
.read_repo_file, and services.local_tools.write_repo_file functions,
and a REAL BINContext built via services.bin_context.build_bin_context
against a REAL, disposable GitHub-connected test project.

This is deliberately NOT a pytest test (no assertion-driven mocking) —
it prints exactly what happened at each step so it can be read as
direct evidence, matching the standard used throughout this
engagement for "live reproduction, not mocks".
"""
import asyncio
import os

from motor.motor_asyncio import AsyncIOMotorClient

from services.bin_context import build_bin_context
from services.local_tools import invoke_local_tool
from services.github_app_config import ensure_configured_from_db

USER_ID = "test_admin_001"
PROJECT_ID = "p_fd18eaaa52"   # polarisbuiltinc-wq/ora-grounding — confirmed in current installation repo list
OUT_OF_SCOPE_PATH = "some/definitely/unread/path/never_touched_2026_02.py"


async def main():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = client[os.environ.get("DB_NAME", "aurem_dev")]

    await ensure_configured_from_db(db)
    bin_ctx = await build_bin_context(USER_ID, PROJECT_ID, db, is_founder=True)
    print(f"[1] Real BINContext built: {bin_ctx.repo_slug()} @ {bin_ctx.branch}")

    ctx = {
        "user_id": USER_ID, "project_id": PROJECT_ID, "bin_ctx": bin_ctx,
        "user_prompt": "update the README",
    }

    # Step A — a REAL read against the REAL GitHub repo (unmocked).
    read_out = await invoke_local_tool("read_repo_file", {"path": "README.md"}, ctx)
    print(f"[2] REAL read_repo_file('README.md') -> ok={read_out.get('ok')}, "
          f"content_len={len(read_out.get('content') or '') if isinstance(read_out, dict) else 'n/a'}")
    print(f"    ctx['scope_files'] after real read = {ctx.get('scope_files')}")

    # Step B — attempt a REAL write to a path that was NEVER read/listed/
    # named this turn. Must be blocked BEFORE reaching write_repo_file /
    # GitHub — proven by: (1) the returned error_class, (2) the path
    # never appearing in ctx['tool_calls'] (the real dispatch-audit
    # trail), (3) no exception/network call to GitHub's contents API.
    tool_calls_before = len(ctx.get("tool_calls", []))
    blocked_out = await invoke_local_tool(
        "write_repo_file",
        {"path": OUT_OF_SCOPE_PATH, "content": "# should never be written\n"},
        ctx,
    )
    tool_calls_after = len(ctx.get("tool_calls", []))
    print(f"[3] REAL write_repo_file('{OUT_OF_SCOPE_PATH}') attempt -> {blocked_out}")
    print(f"    tool_calls audit trail entries: before={tool_calls_before}, after={tool_calls_after}"
          f" (unchanged == the write never actually dispatched to GitHub)")
    assert blocked_out.get("ok") is False, "EXPECTED BLOCK, GOT SUCCESS — REGRESSION"
    assert blocked_out.get("error_class") == "scope_blocked", f"wrong error_class: {blocked_out}"
    assert tool_calls_after == tool_calls_before, "a blocked write must never be recorded as a real dispatch"
    print("[3] ✅ CONFIRMED: out-of-scope write was blocked BEFORE reaching the real tool / GitHub.")

    # Step C — the converse: the SAME path, now in-scope (just read in
    # step A), passes the scope check deterministically (does not raise
    # / does not get scope_blocked). We deliberately do NOT execute a
    # real write here to avoid leaving a needless commit on the shared
    # disposable test repo — the scope decision itself is what's under
    # test, and it's evaluated identically regardless of what fn() does
    # after it (already covered for the actual write execution by the
    # existing mocked unit tests in test_2026_02_context_confusion_
    # scope_gate.py, which assert the real write function DOES get
    # called for an in-scope path).
    from services.local_tools import _check_write_scope
    in_scope_check = _check_write_scope("write_repo_file", {"path": "README.md"}, ctx)
    print(f"[4] Scope check for the JUST-READ path (README.md) -> {in_scope_check} "
          f"(None == would proceed to the real tool, unblocked)")
    assert in_scope_check is None, "in-scope write must NOT be blocked — REGRESSION"
    print("[4] ✅ CONFIRMED: a path actually read this turn is correctly treated as in-scope.")

    print("\nALL LIVE PART-C CHECKS PASSED — real BINContext, real GitHub read, real tool dispatcher.")


if __name__ == "__main__":
    asyncio.run(main())
