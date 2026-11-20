"""
tests/test_regression_iter2026_67_harness_release_status.py — Phase B
readiness check: GET /api/harness/release-status.

Covers:
- {"available": False} when no published release exists (current
  real state — D1 "hosting a real package" is not approved yet, per
  routers/admin_harness.py's own docstring).
- full metadata (version/sha256/changelog/mandatory/size_bytes/
  published_at) when a release IS published — and the raw artifact
  bytes are never included.
- no auth required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from server import app


@pytest.mark.asyncio
async def test_release_status_unavailable_by_default():
    with patch("routers.harness.require_db", return_value=object()), \
         patch("routers.harness.get_latest_release", new=AsyncMock(return_value=None)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/harness/release-status")
    assert resp.status_code == 200
    assert resp.json() == {"available": False}


@pytest.mark.asyncio
async def test_release_status_returns_metadata_when_published():
    fake_release = {
        "release_id": "rel_abc123",
        "version": "0.8.0",
        "platform": "windows",
        "artifact_sha256": "deadbeef" * 8,
        "changelog": ["Initial public Windows build"],
        "mandatory": False,
        "size_bytes": 292519,
        "published_at": 1762819200.0,
        "status": "published",
        "is_latest": True,
    }
    with patch("routers.harness.require_db", return_value=object()), \
         patch("routers.harness.get_latest_release", new=AsyncMock(return_value=fake_release)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/harness/release-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["version"] == "0.8.0"
    assert body["sha256"] == "deadbeef" * 8
    assert body["changelog"] == ["Initial public Windows build"]
    assert "artifact_bytes" not in body
    assert "release_id" not in body


@pytest.mark.asyncio
async def test_release_status_requires_no_auth():
    with patch("routers.harness.require_db", return_value=object()), \
         patch("routers.harness.get_latest_release", new=AsyncMock(return_value=None)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/harness/release-status")
    assert resp.status_code not in (401, 403)
@pytest.mark.asyncio
async def test_release_status_reports_exe_fields_when_attached():
    """PR #342 — a release with an attached .exe reports the exe fields
    so /harness can show the .exe as the primary download."""
    fake_release = {
        "release_id": "rel_abc123",
        "version": "0.8.0",
        "platform": "windows",
        "artifact_sha256": "deadbeef" * 8,
        "changelog": ["Initial public Windows build"],
        "mandatory": False,
        "size_bytes": 292519,
        "published_at": 1762819200.0,
        "status": "published",
        "is_latest": True,
        "exe_sha256": "aabbccdd" * 8,
        "exe_size_bytes": 123456,
    }
    with patch("routers.harness.require_db", return_value=object()), \
            patch("routers.harness.get_latest_release", new=AsyncMock(return_value=fake_release)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/harness/release-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exe_available"] is True
    assert body["exe_sha256"] == "aabbccdd" * 8
    assert body["exe_size_bytes"] == 123456


@pytest.mark.asyncio
async def test_release_status_reports_exe_unavailable_when_not_attached():
    fake_release = {
        "release_id": "rel_abc123",
        "version": "0.8.0",
        "platform": "windows",
        "artifact_sha256": "deadbeef" * 8,
        "changelog": ["Initial public Windows build"],
        "mandatory": False,
        "size_bytes": 292519,
        "published_at": 1762819200.0,
        "status": "published",
        "is_latest": True,
    }
    with patch("routers.harness.require_db", return_value=object()), \
            patch("routers.harness.get_latest_release", new=AsyncMock(return_value=fake_release)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/harness/release-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exe_available"] is False
    assert body["exe_sha256"] is None
    assert body["exe_size_bytes"] is None


@pytest.mark.asyncio
async def test_release_status_marks_the_signed_in_zip_as_personalized():
    """Regression: the signed-in zip is personalized per installation, so
    the file a user downloads does not match the base-artifact sha256 /
    size_bytes; the record must say so and point at the base artifact."""
    fake_release = {
        "version": "1.4.0",
        "platform": "windows",
        "artifact_sha256": "deadbeef" * 8,
        "changelog": ["x"],
        "mandatory": False,
        "size_bytes": 292519,
        "published_at": "2026-02-02T00:00:00+00:00",
        "exe_sha256": "aabbccdd" * 8,
        "exe_size_bytes": 123456,
    }
    transport = ASGITransport(app=app)
    with patch("routers.harness.require_db", return_value=object()), patch(
        "routers.harness.get_latest_release",
        new=AsyncMock(return_value=fake_release),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/harness/release-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["zip_download_personalized"] is True
    assert body["base_package_sha256"] == "deadbeef" * 8
    assert body["base_package_sha256"] == body["sha256"]
    assert body["base_package_size_bytes"] == 292519
    assert body["base_package_size_bytes"] == body["size_bytes"]
