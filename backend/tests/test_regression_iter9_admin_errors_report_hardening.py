"""
test_regression_iter9_admin_errors_report_hardening.py

PR A · item 1 — POST /api/aurem-dev/admin/errors/report
(routers/admin_public.py) was unauthenticated, exempt from the global
rate limiter, and had NO per-IP throttle, NO cap on how many distinct
(message, url) rows a script could create, and no allowlist on
`type`. Any anonymous caller could grow `frontend_errors` without
bound.

Fix: an endpoint-local per-IP limiter (30/min) that returns HTTP 200
`{"ok": false, "error": "rate_limited"}` (never 429 — the frontend
ErrorBoundary/console hook retries on failure and a non-2xx here would
storm the endpoint harder, not calm it), a global cap on NEW distinct
rows created per rolling hour (existing (message,url) rows can still
be updated for free), a `type` allowlist, lower field caps, and every
row stamped `trusted: false` (this sink has no auth — nothing it
writes is ever admin-originated).

Uses the real Mongo instance (MONGO_URL) like
test_regression_iter1_login_lockout_respects_rate_limit_disabled.py —
the endpoint's own rate-limit/new-row-cap logic needs a faithful
find_one + update_one(upsert) + count_documents round-trip, which a
hand-rolled fake collection would just re-implement badly.
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

import services.rate_limiter as rl


@pytest.fixture(autouse=True)
def _rl_reset():
    rl.reset_buckets_for_tests()
    yield
    rl.reset_buckets_for_tests()


@pytest.fixture
async def _errors_db(monkeypatch):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    import routers.admin_public as ap
    monkeypatch.setattr(ap, "get_db", lambda: db)
    yield db
    # Clean up everything this file created, tagged by URL prefix.
    await db.frontend_errors.delete_many({"url": {"$regex": "^https://test.iter9/"}})
    client.close()


async def _post(app, message, url, headers, extra=None):
    body = {"message": message, "url": url}
    if extra:
        body.update(extra)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        return await ac.post("/api/aurem-dev/admin/errors/report",
                             json=body, headers=headers)


@pytest.mark.asyncio
async def test_burst_from_one_ip_is_rate_limited_after_30(_errors_db, monkeypatch):
    """30 requests/min per IP is the cap. The 31st must be refused —
    but with HTTP 200, not 429 (see next test for why that matters)."""
    from main import app
    headers = {"X-Forwarded-For": "203.0.113.201"}
    codes_and_bodies = []
    for i in range(31):
        r = await _post(app, f"burst msg {i}",
                        f"https://test.iter9/burst/{i}", headers)
        codes_and_bodies.append((r.status_code, r.json()))

    allowed = [b for _, b in codes_and_bodies[:30]]
    assert all(b.get("ok") is True for b in allowed), codes_and_bodies[:30]

    last_status, last_body = codes_and_bodies[30]
    assert last_status == 200, (
        f"31st request over the per-IP cap must still be HTTP 200 "
        f"(got {last_status}) — see next test for the retry-storm reason"
    )
    assert last_body == {"ok": False, "error": "rate_limited"}


@pytest.mark.asyncio
async def test_error_boundary_retry_path_never_gets_a_non_2xx(_errors_db, monkeypatch):
    """The frontend's errorReporter.js retries failed sends via
    sendBeacon/keepalive fetch. If a rate-limited response were ever a
    429, those retries would hammer the endpoint harder, not back off.
    Every response — allowed or throttled — must be HTTP 200."""
    from main import app
    headers = {"X-Forwarded-For": "203.0.113.202"}
    statuses = set()
    for i in range(40):
        r = await _post(app, "same repeated error",
                        "https://test.iter9/retry-path", headers)
        statuses.add(r.status_code)
    assert statuses == {200}, f"saw non-200 statuses: {statuses}"


@pytest.mark.asyncio
async def test_invalid_type_is_coerced_not_rejected(_errors_db):
    """`type` is allowlisted to the 3 values errorReporter.js actually
    sends; anything else is coerced to the safe default rather than
    bouncing the report (this is a diagnostics sink, not a strict API)."""
    from main import app
    headers = {"X-Forwarded-For": "203.0.113.203"}
    r = await _post(app, "type coercion test",
                    "https://test.iter9/type-coerce", headers,
                    extra={"type": "<script>alert(1)</script>"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    doc = await _errors_db.frontend_errors.find_one(
        {"url": "https://test.iter9/type-coerce"})
    assert doc["type"] == "console_error"


@pytest.mark.asyncio
async def test_every_row_is_stamped_untrusted(_errors_db):
    """This sink has no auth. Every row it writes must carry
    trusted=False so the admin Errors UI never treats message/stack
    as anything other than plain, unauthenticated text."""
    from main import app
    headers = {"X-Forwarded-For": "203.0.113.204"}
    r = await _post(app, "trust flag test",
                    "https://test.iter9/trust-flag", headers)
    assert r.status_code == 200

    doc = await _errors_db.frontend_errors.find_one(
        {"url": "https://test.iter9/trust-flag"})
    assert doc["trusted"] is False


@pytest.mark.asyncio
async def test_field_sizes_are_capped(_errors_db):
    """Lowered caps (message<=1000, stack<=4000, url<=400) — an
    attacker sending a multi-KB payload must be truncated, not stored
    whole."""
    from main import app
    headers = {"X-Forwarded-For": "203.0.113.205"}
    r = await _post(
        app, "x" * 5_000,
        "https://test.iter9/cap?" + ("y" * 2_000), headers,
        extra={"stack": "z" * 20_000},
    )
    assert r.status_code == 200

    doc = await _errors_db.frontend_errors.find_one(
        {"message": {"$regex": "^x{100}"}})
    assert doc is not None
    assert len(doc["message"]) <= 1_000
    assert len(doc["url"]) <= 400
    assert len(doc["stack"]) <= 4_000


@pytest.mark.asyncio
async def test_global_hourly_new_row_cap_blocks_further_distinct_rows(
        _errors_db, monkeypatch):
    """Repeat-reporting an EXISTING (message, url) is always free (no
    new row). Once the global new-distinct-rows-per-hour budget is
    spent, the NEXT never-seen (message, url) pair must be refused
    with the same retry-safe 200/{"ok": false} shape — without this,
    a script posting random urls could grow the collection without
    bound."""
    from main import app
    import routers.admin_public as ap
    monkeypatch.setattr(ap, "_MAX_NEW_ROWS_PER_HOUR", 2)
    monkeypatch.setattr(ap, "_PER_IP_LIMIT_PER_MIN", 1000)

    # The hourly cap is a GLOBAL counter over all rows created in the
    # trailing hour, not scoped to this test — clear the window first
    # so leftover rows from other tests/manual runs in this same dev
    # DB don't make the (monkeypatched, tiny) budget look already spent.
    from datetime import datetime, timezone, timedelta
    from bson import ObjectId
    hour_ago = ObjectId.from_datetime(datetime.now(timezone.utc) - timedelta(hours=1))
    await _errors_db.frontend_errors.delete_many({"_id": {"$gte": hour_ago}})

    headers = {"X-Forwarded-For": "203.0.113.206"}
    r1 = await _post(app, "cap test row 1",
                     "https://test.iter9/hourcap/1", headers)
    r2 = await _post(app, "cap test row 2",
                     "https://test.iter9/hourcap/2", headers)
    assert r1.json()["ok"] is True and r2.json()["ok"] is True

    # Re-reporting an EXISTING row must stay free regardless of the cap.
    r1_again = await _post(app, "cap test row 1",
                           "https://test.iter9/hourcap/1", headers)
    assert r1_again.json()["ok"] is True

    # A THIRD never-seen row is over the (monkeypatched) budget of 2.
    r3 = await _post(app, "cap test row 3",
                     "https://test.iter9/hourcap/3", headers)
    assert r3.status_code == 200
    assert r3.json() == {"ok": False, "error": "rate_limited"}
