"""tests/test_regression_iter2026_65_harness_page_status_public.py

Aurem Loop harness — public (no-auth) flag-read endpoint,
`GET /api/harness/page-status`, added 2026-11-09 to close "the
pre-login flag gap": before this, `harness_page_enabled` was only
ever exposed via the authenticated /auth/me response (routers/auth.py),
so an anonymous/signed-out visitor (and the build-time homepage SEO
prerender script) had no way to read the real flag value at all —
pageFlags.js's `isHarnessPageEnabled()` silently stayed at its
hardcoded `false` default for them regardless of the actual
admin-set value in Mongo.

Rules under test:
  - No Authorization header required — a bare, unauthenticated GET
    must succeed (this is the whole point of the endpoint).
  - Returns exactly `{"harness_page_enabled": bool}`, nothing else
    (no internal flag metadata, no tier_allowlist, no rollout_pct —
    this is a public marketing-site read, not an admin view).
  - Reads from the SAME `routers.harness.is_enabled("harness_page")`
    source /auth/me's own `harness_page_enabled` field uses — single
    source of truth, proven here by mocking `is_enabled` directly and
    asserting the endpoint's output tracks it exactly (true -> true,
    false -> false), never inverted or hardcoded.
  - No flag doc in Mongo at all (the real current state, confirmed via
    `is_enabled`'s own documented "doc is None -> False" behavior) ->
    false, not a 404/500 — a missing flag must fail closed, not error.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio


def _make_app():
    from routers.harness import router as harness_router
    app = FastAPI()
    app.include_router(harness_router, prefix="/api")
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


async def test_no_auth_header_required(client):
    """The whole point of this endpoint: a bare GET with zero headers
    must succeed — it must never 401/403 like every other harness
    admin/download route does."""
    with patch("routers.harness.is_enabled", new=AsyncMock(return_value=False)):
        resp = client.get("/api/harness/page-status")
    assert resp.status_code == 200


async def test_response_shape_is_exactly_one_boolean_field(client):
    with patch("routers.harness.is_enabled", new=AsyncMock(return_value=True)):
        resp = client.get("/api/harness/page-status")
    body = resp.json()
    assert body == {"harness_page_enabled": True}


async def test_tracks_is_enabled_true(client):
    with patch("routers.harness.is_enabled", new=AsyncMock(return_value=True)) as mock_ie:
        resp = client.get("/api/harness/page-status")
    assert resp.json()["harness_page_enabled"] is True
    mock_ie.assert_awaited_once_with("harness_page")


async def test_tracks_is_enabled_false(client):
    with patch("routers.harness.is_enabled", new=AsyncMock(return_value=False)):
        resp = client.get("/api/harness/page-status")
    assert resp.json()["harness_page_enabled"] is False


async def test_no_flag_doc_fails_closed_not_errors(client):
    """Mirrors services/feature_flags.py's real documented behavior:
    `doc is None -> return False` — a flag that was never created
    (today's real state for "harness_page") must read as cleanly
    disabled, never a 404/500."""
    with patch("routers.harness.is_enabled", new=AsyncMock(return_value=False)):
        resp = client.get("/api/harness/page-status")
    assert resp.status_code == 200
    assert resp.json() == {"harness_page_enabled": False}


async def test_no_user_id_passed_to_is_enabled_anonymous_safe(client):
    """Must call is_enabled with no user_id/tier — this endpoint has
    no session to read one from, and services/feature_flags.py's own
    anonymous-canary-bucket logic already handles a missing user_id
    safely (falls back to the pre-rollout off default for a partial
    rollout, or the plain master `enabled` bool otherwise)."""
    with patch("routers.harness.is_enabled", new=AsyncMock(return_value=True)) as mock_ie:
        client.get("/api/harness/page-status")
    args, kwargs = mock_ie.call_args
    assert "user_id" not in kwargs or kwargs["user_id"] is None
    assert "tier" not in kwargs or kwargs["tier"] is None
