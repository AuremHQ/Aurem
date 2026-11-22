"""Nov 2026 prod incident (2026-11-21): GET /aurem-dev/admin/status/all
    hung because a check_fn blocked the event loop with sync I/O. The 20s
    outer aggregator timeout could only fire after the block released the
    loop, so while it was stuck EVERY request stalled, including /health,
    and nginx logged ~28 upstream timeouts on GET /health in one minute.

    Fix under test: every check now runs in a dedicated thread pool on its
    own event loop (routers.admin_health._run_check_threaded), with a
    per-check timeout enforced from the request loop. A blocking check
    degrades to its own red result; the event loop -- and /health -- stay
    responsive.

    The tests register real (blocking) check_fns in the real registry and
    exercise the real status_all handler / real router. Only the production
    checks are swapped out (monkeypatched all_checks) so the tests are
    hermetic and fast instead of hitting live services from the test
    container.
"""

import asyncio
import time

import httpx
import pytest
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv("/app/backend/.env")

import routers.admin_health as admin_health  # noqa: E402
from routers.admin_health import _STATUS_CACHE, router as admin_health_router  # noqa: E402
from routers.admin_health import status_all  # noqa: E402
from services.health_registry import _REGISTRY, register_check  # noqa: E402

BLOCKER_TAG = "__test_blocker_2026_11_21"
HEALTHY_TAG = "__test_healthy_2026_11_21"


def _clear_cache():
    _STATUS_CACHE["payload"] = None
    _STATUS_CACHE["expires_at"] = 0.0


async def _blocking_check():
    # NO await -- pure sync block, the exact prod failure mode
    # (a check doing time.sleep / requests / a hung socket on the loop).
    # 15s: long past the 8s per-check timeout, short enough to keep the
    # suite fast. Without the threadpool fix the aggregator would sit
    # on the blocked loop for the full 15s.
    time.sleep(15.0)
    return {"status": "green", "detail": "should have been cancelled", "checked_at": "test"}


async def _healthy_check():
    return {"status": "green", "detail": "ok", "checked_at": "test"}


@pytest.fixture
def _two_checks(monkeypatch):
    """Register only the two test checks and make the aggregator use
    exactly those (the ~29 production checks are not run)."""
    register_check(BLOCKER_TAG, "blocker", "infra", _blocking_check)
    register_check(HEALTHY_TAG, "healthy", "infra", _healthy_check)
    checks = [_REGISTRY[BLOCKER_TAG], _REGISTRY[HEALTHY_TAG]]
    monkeypatch.setattr(admin_health, "all_checks", lambda: list(checks))
    _clear_cache()
    yield checks
    _REGISTRY.pop(BLOCKER_TAG, None)
    _REGISTRY.pop(HEALTHY_TAG, None)
    _clear_cache()


def _by_id(payload):
    return {row["id"]: row for row in payload["checks"]}


@pytest.mark.asyncio
async def test_blocking_check_degrades_to_red_and_aggregator_returns_promptly(_two_checks):
    """A check that blocks the event loop with sync I/O must degrade to
    its own red result at the per-check timeout, and status_all must
    still return promptly -- without tripping the outer timeout and
    without affecting the healthy check. Scoring is unchanged: one
    green + one red out of two checks is health_pct 50.0."""
    t0 = time.monotonic()
    payload = await status_all()
    took = time.monotonic() - t0

    assert took < 12.0, f"status/all took {took:.1f}s with a blocking check"
    assert not payload.get("aggregator_timeout"), "outer timeout must not trip for one bad check"
    assert payload["counts"] == {"green": 1, "red": 1, "gray": 0, "total": 2, "health_pct": 50.0}
    by_id = _by_id(payload)
    assert by_id[BLOCKER_TAG]["status"] == "red"
    assert "timed out" in by_id[BLOCKER_TAG]["detail"].lower()
    assert by_id[BLOCKER_TAG]["check_ms"] >= 7000
    assert by_id[HEALTHY_TAG]["status"] == "green"
    assert by_id[HEALTHY_TAG]["check_ms"] < 5000


@pytest.mark.asyncio
async def test_health_answers_while_check_blocks(_two_checks):
    """While status/all is waiting on a blocking check, a liveness
    request on the same event loop must still answer promptly. The
    probe below is the equivalent of GET /health (trivial handler,
    no I/O -- the real /health in main.py is the same shape); in
    production the blocked loop is what made /health time out, not
    /health itself."""
    from cto_services.auth import require_admin_dep

    app = FastAPI()
    app.include_router(admin_health_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # The endpoint under test is the aggregator, not admin auth.
    app.dependency_overrides[require_admin_dep] = lambda: None

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _clear_cache()
        status_task = asyncio.create_task(client.get("/aurem-dev/admin/status/all"))
        # Let the aggregator start the blocking check first.
        await asyncio.sleep(0.5)
        t0 = time.monotonic()
        health_resp = await client.get("/health")
        health_took = time.monotonic() - t0
        assert health_resp.status_code == 200, health_resp.text
        assert health_took < 2.0, f"/health took {health_took:.2f}s while a check blocked"
        status_resp = await status_task
        assert status_resp.status_code == 200, status_resp.text
        by_id = _by_id(status_resp.json())
        assert by_id[BLOCKER_TAG]["status"] == "red"
        assert by_id[HEALTHY_TAG]["status"] == "green"
