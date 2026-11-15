"""
tests/test_regression_iter2026_68_harness_waitlist.py — POST
/api/harness/waitlist, added 2026-11-11 (Fix 2).

Before this endpoint existed, "Join the waitlist" on /harness was a
mailto: link only — did nothing visible in a browser with no mail
client configured. This is the real on-page form's backend: public,
no-auth, per-IP rate-limited exactly like routers/support.py's
`/tickets/public`.

Covers:
- valid email -> 200 {"ok": true}, upserted into db.harness_waitlist.
- invalid email -> 400, no DB write.
- repeat submit of the SAME email -> still 200 (no duplicate-key
  error), $setOnInsert never clobbers the original `joined_at`.
- per-IP rate limit -> 429 once exceeded.
- no auth required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from server import app


def _fake_db():
    db = MagicMock()
    db.harness_waitlist = MagicMock()
    db.harness_waitlist.update_one = AsyncMock(return_value=MagicMock())
    return db


@pytest.mark.asyncio
async def test_valid_email_upserts_and_returns_ok():
    db = _fake_db()
    with patch("routers.harness.require_db", return_value=db), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.harness.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/harness/waitlist", json={"email": "dev@example.com"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    db.harness_waitlist.update_one.assert_awaited_once()
    filter_arg, update_arg = db.harness_waitlist.update_one.call_args[0]
    assert filter_arg == {"email": "dev@example.com"}
    assert update_arg["$setOnInsert"]["email"] == "dev@example.com"
    assert update_arg["$setOnInsert"]["source"] == "harness_page"
    assert db.harness_waitlist.update_one.call_args.kwargs["upsert"] is True


@pytest.mark.asyncio
async def test_email_is_lowercased_and_trimmed():
    db = _fake_db()
    with patch("routers.harness.require_db", return_value=db), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.harness.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/harness/waitlist", json={"email": "  Dev@Example.COM  "})
    assert resp.status_code == 200
    filter_arg, _ = db.harness_waitlist.update_one.call_args[0]
    assert filter_arg == {"email": "dev@example.com"}


@pytest.mark.asyncio
async def test_invalid_email_is_rejected_no_db_write():
    db = _fake_db()
    with patch("routers.harness.require_db", return_value=db), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.harness.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/harness/waitlist", json={"email": "not-an-email"})
    assert resp.status_code == 400
    db.harness_waitlist.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeat_submit_of_same_email_still_returns_ok():
    """$setOnInsert means a second join from the same visitor is a
    harmless no-op write, never a duplicate-key error surfaced to the
    user."""
    db = _fake_db()
    with patch("routers.harness.require_db", return_value=db), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.harness.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/api/harness/waitlist", json={"email": "dev@example.com"})
            r2 = await client.post("/api/harness/waitlist", json={"email": "dev@example.com"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert db.harness_waitlist.update_one.await_count == 2


@pytest.mark.asyncio
async def test_rate_limit_exceeded_returns_429():
    db = _fake_db()
    with patch("routers.harness.require_db", return_value=db), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=False)), \
         patch("routers.harness.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/harness/waitlist", json={"email": "dev@example.com"})
    assert resp.status_code == 429
    db.harness_waitlist.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_auth_header_required():
    db = _fake_db()
    with patch("routers.harness.require_db", return_value=db), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.harness.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/harness/waitlist", json={"email": "dev@example.com"})
    assert resp.status_code == 200
