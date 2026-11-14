"""
tests/test_dot_signup.py — POST /api/dot/signup + GET
/api/aurem-dev/admin/dot/signups, added 2026-11-14 (ORA dot page).

The /dot page's "Join the list" form: public, no-auth, per-IP
rate-limited exactly like routers/harness.py's /harness/waitlist
(Fix 2). Upserts on email into db.dot_signups — a repeat submit never
creates a duplicate row, `joined_at` keeps the first-join date, `name`
is refreshed on re-submit.

Covers:
- valid name + email -> 200 {"ok": true}, upserted into db.dot_signups.
- email lowercased/trimmed, name whitespace-collapsed.
- blank name / invalid email -> 400, no DB write.
- repeat submit of the SAME email -> still 200 (no duplicate-key
  error), $setOnInsert never clobbers the original `joined_at`.
- per-IP rate limit -> 429 once exceeded.
- no auth required.
- admin list -> 200 with rows + total (admin dependency overridden in
  the test; the real gate is the router-level require_admin_dep, same
  one-liner as routers/admin_harness.py).
- admin CSV export -> 200 text/csv with a header row + one row per signup.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from cto_services.auth import require_admin_dep
from server import app


def _fake_db():
    db = MagicMock()
    db.dot_signups = MagicMock()
    db.dot_signups.update_one = AsyncMock(return_value=MagicMock())
    return db


def _fake_cursor(rows):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


@pytest.mark.asyncio
async def test_valid_name_and_email_upserts_and_returns_ok():
    db = _fake_db()
    with patch("routers.dot.require_db", return_value=db), \
         patch("routers.dot.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.dot.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dot/signup", json={"name": "Teji", "email": "dev@example.com"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    db.dot_signups.update_one.assert_awaited_once()
    filter_arg, update_arg = db.dot_signups.update_one.call_args[0]
    assert filter_arg == {"email": "dev@example.com"}
    assert update_arg["$setOnInsert"]["email"] == "dev@example.com"
    assert update_arg["$setOnInsert"]["source"] == "dot_page"
    assert update_arg["$set"]["name"] == "Teji"
    assert db.dot_signups.update_one.call_args.kwargs["upsert"] is True


@pytest.mark.asyncio
async def test_email_is_lowercased_and_trimmed_and_name_collapsed():
    db = _fake_db()
    with patch("routers.dot.require_db", return_value=db), \
         patch("routers.dot.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.dot.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dot/signup", json={"name": "  Teji   Sandhu  ", "email": "  Dev@Example.COM  "})
    assert resp.status_code == 200
    filter_arg, update_arg = db.dot_signups.update_one.call_args[0]
    assert filter_arg == {"email": "dev@example.com"}
    assert update_arg["$set"]["name"] == "Teji Sandhu"


@pytest.mark.asyncio
async def test_blank_name_is_rejected_no_db_write():
    db = _fake_db()
    with patch("routers.dot.require_db", return_value=db), \
         patch("routers.dot.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.dot.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dot/signup", json={"name": "   ", "email": "dev@example.com"})
    assert resp.status_code == 400
    db.dot_signups.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_email_is_rejected_no_db_write():
    db = _fake_db()
    with patch("routers.dot.require_db", return_value=db), \
         patch("routers.dot.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.dot.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dot/signup", json={"name": "Teji", "email": "not-an-email"})
    assert resp.status_code == 400
    db.dot_signups.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeat_submit_of_same_email_still_returns_ok():
    """$setOnInsert means a second join from the same visitor is a
    harmless no-op write, never a duplicate-key error surfaced to the
    user."""
    db = _fake_db()
    with patch("routers.dot.require_db", return_value=db), \
         patch("routers.dot.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.dot.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/api/dot/signup", json={"name": "Teji", "email": "dev@example.com"})
            r2 = await client.post("/api/dot/signup", json={"name": "Teji Sandhu", "email": "dev@example.com"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert db.dot_signups.update_one.await_count == 2


@pytest.mark.asyncio
async def test_rate_limit_exceeded_returns_429():
    db = _fake_db()
    with patch("routers.dot.require_db", return_value=db), \
         patch("routers.dot.check_rate_limit_async", new=AsyncMock(return_value=False)), \
         patch("routers.dot.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dot/signup", json={"name": "Teji", "email": "dev@example.com"})
    assert resp.status_code == 429
    db.dot_signups.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_auth_header_required():
    db = _fake_db()
    with patch("routers.dot.require_db", return_value=db), \
         patch("routers.dot.check_rate_limit_async", new=AsyncMock(return_value=True)), \
         patch("routers.dot.client_ip_from_request", return_value="1.2.3.4"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dot/signup", json={"name": "Teji", "email": "dev@example.com"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_list_returns_rows_and_total():
    rows = [
        {"name": "Teji", "email": "dev@example.com", "joined_at": 1763112000, "source": "dot_page"},
        {"name": "Asha", "email": "asha@example.com", "joined_at": 1763025600, "source": "dot_page"},
    ]
    db = _fake_db()
    db.dot_signups.find.return_value = _fake_cursor(rows)
    app.dependency_overrides[require_admin_dep] = lambda: {"email": "owner@example.com", "is_admin": True}
    try:
        with patch("routers.admin_dot.require_db", return_value=db):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/aurem-dev/admin/dot/signups")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["signups"][0]["email"] == "dev@example.com"
    assert body["signups"][1]["name"] == "Asha"


@pytest.mark.asyncio
async def test_admin_export_returns_csv():
    rows = [
        {"name": "Teji", "email": "dev@example.com", "joined_at": 1763112000, "source": "dot_page"},
    ]
    db = _fake_db()
    db.dot_signups.find.return_value = _fake_cursor(rows)
    app.dependency_overrides[require_admin_dep] = lambda: {"email": "owner@example.com", "is_admin": True}
    try:
        with patch("routers.admin_dot.require_db", return_value=db):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/aurem-dev/admin/dot/signups/export")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "name,email,joined_at" in resp.text
    assert "dev@example.com" in resp.text
