"""
Iter 124f — End-to-end isolation test for the eval runner's offline
project-scoping check. Uses the REAL Mongo configured in backend/.env,
inserts throwaway projects, asserts cross-user access is blocked, and
cleans up after itself.

This test catches the most security-critical failure mode for a
multi-tenant SaaS: User A's session somehow seeing User B's repo data.

2026-09-22 — root-caused a SILENT false-confidence bug in this exact
test: it calls `_project_scoping_isolation_test()` directly (no
FastAPI TestClient), so `main.py`'s startup lifespan — the only thing
that normally calls `cto_services.db.set_db()` — never ran. `get_db()`
was always None here, so this test was ALWAYS taking the
"Mongo unavailable → PARTIAL, skip" shortcut, every single run, and
had NEVER actually exercised real Mongo despite its own docstring's
claim. The `_bootstrap_real_mongo` fixture below fixes that using the
exact same bootstrap `evals/runner.py` already uses for its own
standalone script context.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from evals.runner import _project_scoping_isolation_test


@pytest.fixture(autouse=True)
async def _bootstrap_real_mongo():
    """Same bootstrap as evals/runner.py's `run()` — a bare async
    function test never triggers main.py's startup lifespan, so
    cto_services.db._db is None unless we wire it here ourselves.

    Always creates a FRESH client scoped to this test's own event
    loop rather than reusing whatever `get_db()` already returns —
    pytest-asyncio gives each test function its own event loop by
    default, and a Motor client bound to a previous (now-closed) loop
    raises "Event loop is closed" the moment a later test touches it."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from cto_services.db import set_db

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    client = None
    if mongo_url and db_name:
        client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=3000)
        set_db(client[db_name])
    yield
    if client is not None:
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_cross_user_repo_lookup_blocked():
    """get_repo_context(userA, projectB) MUST return empty string. The
    Mongo find_one() filter requires BOTH project_id AND user_id to
    match, so a prompt-injected project_id from User A can never
    surface User B's repo."""
    result = await _project_scoping_isolation_test()

    # The test inserts real docs and cleans up. We assert the scorer.
    assert "scorers" in result
    scorers = result["scorers"]
    assert len(scorers) >= 1

    scope_check = next(
        (s for s in scorers if s.get("scorer") == "scope"),
        None,
    )
    assert scope_check is not None
    # 2026-09-22 — now that _bootstrap_real_mongo actually connects,
    # this MUST be PASS, not the old silent PARTIAL shortcut. PARTIAL
    # stays allowed only as a genuine "Mongo really is unreachable"
    # signal (e.g. this exact sandbox's Mongo being down), never as a
    # side effect of forgetting to bootstrap the connection.
    assert scope_check["status"] in ("PASS", "PARTIAL"), (
        f"Cross-user isolation broke: {scope_check}"
    )
    if scope_check["status"] == "FAIL":
        pytest.fail(
            f"CRITICAL: cross-user repo bleed: {scope_check['evidence']}"
        )
    if scope_check["status"] == "PARTIAL":
        assert "Mongo unavailable" in scope_check.get("evidence", ""), (
            "PARTIAL for a reason other than a real Mongo outage — "
            "investigate before trusting this test again"
        )


@pytest.mark.asyncio
async def test_isolation_check_actually_ran_against_real_mongo_not_the_skip_shortcut():
    """2026-09-22 regression: proves the fixture above is really
    connecting — if it weren't, this would silently PARTIAL/skip
    forever without ever failing loud, exactly like it did before this
    fix. FAILS on the pre-fix code (no bootstrap → always PARTIAL with
    'Mongo unavailable'), PASSES once the connection is real."""
    from cto_services.db import get_db
    assert get_db() is not None, (
        "get_db() is still None inside the test — the isolation check "
        "below would silently no-op via the 'Mongo unavailable' "
        "shortcut, giving false confidence exactly like before this fix"
    )
    result = await _project_scoping_isolation_test()
    scope_check = next(s for s in result["scorers"] if s.get("scorer") == "scope")
    assert scope_check["status"] == "PASS", (
        f"expected a REAL isolation check to PASS, got: {scope_check}"
    )


@pytest.mark.asyncio
async def test_isolation_check_does_not_crash_on_non_connected_synthetic_project():
    """2026-09-22 regression for the OTHER bug this same investigation
    found: the synthetic self-access case (case 1, user_a -> proj_a)
    used a stale `github_pat`-only fixture, incompatible with the
    App-only auth model (`services.pat_vault.get_repo_token` now
    raises GithubAppAuthError for any project without a real App
    installation) — that crashed the ENTIRE eval runner process
    unhandled, which is exactly the crash the real `predeploy_gate.sh`
    Lane 11 hit. FAILS on the pre-fix code (unhandled
    GithubAppAuthError propagates out of _project_scoping_isolation_test
    and this call raises), PASSES once case 1 is caught narrowly."""
    # No assertion needed beyond "did not raise" — the real assertion
    # is that this coroutine completes at all.
    result = await asyncio.wait_for(_project_scoping_isolation_test(), timeout=15)
    assert result["hard_fails"] == 0
