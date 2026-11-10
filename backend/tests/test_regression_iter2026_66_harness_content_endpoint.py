"""
tests/test_regression_iter2026_66_harness_content_endpoint.py — Phase A
content delivery: GET /api/harness/content.

Covers:
- fails closed to {"available": False} when nothing has been fetched
  successfully yet (no token / repo content missing) — never a 500,
  never fabricated copy.
- live_stats (wsp-kit stars/forks) is returned best-effort alongside
  content and never blocks the response if GitHub is unreachable.
- the endpoint does not require auth.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from server import app


@pytest.mark.asyncio
async def test_harness_content_fails_closed_when_repo_fetch_fails():
    with patch(
        "routers.harness.fetch_harness_page_content",
        new=AsyncMock(return_value={"available": False}),
    ), patch(
        "routers.harness.fetch_wsp_kit_live_stats",
        new=AsyncMock(return_value={"stars": 16, "forks": 2}),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/harness/content")
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == {"available": False}
    assert body["live_stats"] == {"stars": 16, "forks": 2}


@pytest.mark.asyncio
async def test_harness_content_returns_full_content_when_available():
    fake_content = {"available": True, "title": "Aurem Loop — local coding harness"}
    with patch(
        "routers.harness.fetch_harness_page_content",
        new=AsyncMock(return_value=fake_content),
    ), patch(
        "routers.harness.fetch_wsp_kit_live_stats",
        new=AsyncMock(return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/harness/content")
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"]["available"] is True
    assert body["content"]["title"] == "Aurem Loop — local coding harness"
    assert body["live_stats"] is None


@pytest.mark.asyncio
async def test_harness_content_requires_no_auth():
    with patch(
        "routers.harness.fetch_harness_page_content",
        new=AsyncMock(return_value={"available": False}),
    ), patch(
        "routers.harness.fetch_wsp_kit_live_stats",
        new=AsyncMock(return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/harness/content")
    assert resp.status_code != 401
    assert resp.status_code != 403
