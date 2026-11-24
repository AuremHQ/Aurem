"""2026-11-24 — admin/cockpit "guards red + score timeout" production
bug. Root cause: admin_health.py's non-blocking aggregator (the
2026-11-21 fix, see test_admin_status_aggregator_nonblocking_2026_11_21.py)
ran every check via `asyncio.run(...)` in a FRESH worker thread, i.e. a
BRAND NEW event loop per check per call. Motor's AsyncIOMotorClient
(the one shared cto_services.db handle) binds internal asyncio.Lock /
Future objects to whichever loop is running the first time it's used;
reusing that SAME client from a different loop crashes with
"Task ... got Future ... attached to a different loop". Every
DB-touching guard (G1, G5, G10, G15, G20, infra_db) showed this crash
as a permanent fake "red", and the increased per-call thread/loop
churn was slow enough to trip the UI's 15s timeout on constrained
production CPU.

Fix under test: cto_services.db.get_db() is loop-aware (returns a
small dedicated secondary client, cached per loop, for any loop other
than the main app loop) AND admin_health.py reuses a small FIXED pool
of persistent background loops instead of creating one per check per
call.
"""
import asyncio

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

import cto_services.db as db_module  # noqa: E402
import routers.admin_health as admin_health  # noqa: E402
from routers.admin_health import _STATUS_CACHE, status_all  # noqa: E402
from services.health_registry import _REGISTRY, register_check  # noqa: E402

DB_CHECK_TAG = "__test_db_touching_2026_11_24"


def _clear_cache():
    _STATUS_CACHE["payload"] = None
    _STATUS_CACHE["expires_at"] = 0.0


async def _db_touching_check():
    """Mirrors the real G1/G5/G10/G15/G20/infra_db shape: fetch the
    db handle *inside* the check coroutine (as every real adapter
    does) and run a real query against it."""
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"status": "gray", "detail": "database unavailable", "checked_at": "test"}
    await db.command("ping")
    return {"status": "green", "detail": "db reachable", "checked_at": "test"}


@pytest.fixture
def _one_db_check(monkeypatch):
    register_check(DB_CHECK_TAG, "db-touching", "infra", _db_touching_check)
    checks = [_REGISTRY[DB_CHECK_TAG]]
    monkeypatch.setattr(admin_health, "all_checks", lambda: list(checks))
    _clear_cache()
    yield checks
    _REGISTRY.pop(DB_CHECK_TAG, None)
    _clear_cache()


@pytest.mark.asyncio
async def test_db_touching_check_does_not_crash_with_different_loop_error(_one_db_check):
    """A check that calls get_db() and awaits a real query, while
    running on admin_health's background loop pool, must NOT crash
    with "attached to a different loop" — it must either succeed
    (green) or degrade honestly (gray/red), never a raw RuntimeError
    leaking into the detail text."""
    # Run status_all() twice — the first run proves no crash on a
    # cold background loop; the second proves the cached secondary
    # client is safely reused on the SAME (already-running) loop.
    for _ in range(2):
        _clear_cache()
        payload = await status_all()
        row = payload["checks"][0]
        assert row["id"] == DB_CHECK_TAG
        assert "different loop" not in (row.get("detail") or "").lower()
        assert "runtimeerror" not in (row.get("detail") or "").lower()
        assert row["status"] in ("green", "gray"), row["detail"]


@pytest.mark.asyncio
async def test_get_db_returns_distinct_handle_per_non_main_loop(_one_db_check):
    """cto_services.db.get_db() must not hand the main-loop-bound
    client to a different loop — it should resolve to a (cached,
    reused) secondary handle instead."""
    import os
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo_url = os.environ.get("MONGO_URL", "")
    if not mongo_url:
        pytest.skip("MONGO_URL not set in this test environment")

    # Simulate the real app's startup: a client created + registered
    # on THIS (the test's / "main") loop.
    main_client = AsyncIOMotorClient(mongo_url)
    main_db = main_client[os.environ.get("DB_NAME", "aurem_dev")]
    saved_db, saved_loop = db_module._db, db_module._main_loop
    db_module.set_db(main_db)
    try:
        assert db_module.get_db() is main_db

        # Fetch get_db() from a pooled background loop directly and
        # confirm it's NOT the same object as the main-loop client —
        # if it were, that's the exact cross-loop sharing that crashed.
        bg_loop = admin_health._ensure_bg_loop(0)

        async def _fetch():
            from cto_services.db import get_db
            return get_db()

        future = asyncio.run_coroutine_threadsafe(_fetch(), bg_loop)
        bg_db = await asyncio.wrap_future(future)
        assert bg_db is not None
        assert bg_db is not main_db

        # Calling it again from the SAME background loop must reuse
        # the cached secondary client, not create a new one each time.
        future2 = asyncio.run_coroutine_threadsafe(_fetch(), bg_loop)
        bg_db_again = await asyncio.wrap_future(future2)
        assert bg_db_again is bg_db
    finally:
        main_client.close()
        db_module._db, db_module._main_loop = saved_db, saved_loop

