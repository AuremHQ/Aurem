"""tests/test_regression_iter2026_78_harness_download_public.py

GET /api/harness/download-public/{version} — the one-line PowerShell
installer's download path (frontend/public/install-aurem-loop.ps1).
Runs as `irm ... | iex` with no Authorization header by design, so it
cannot call the authenticated /aurem-dev/harness/download route; this
serves the same base artifact bytes with no identity.json injected.

Covers:
- serves the artifact + X-Aurem-Package-SHA256 header when the
  requested version matches the currently-published latest release.
- 404 (never serves) when no release is published, OR the requested
  version does not match the actual latest — a stale pinned copy of
  the installer script must fail closed, never silently fetch an
  older/mismatched build.
- 429 on a rate-limited caller, before any DB/release lookup.
- 503 (never a raw crash) if the release row exists but its artifact
  bytes are missing.
- no auth required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from server import app

_FAKE_RELEASE = {
    "release_id": "rel_abc123",
    "version": "0.8.0",
    "platform": "windows",
    "artifact_sha256": "deadbeef" * 8,
    "changelog": ["Initial public Windows build"],
    "mandatory": False,
    "size_bytes": 7,
    "published_at": 1762819200.0,
    "status": "published",
    "is_latest": True,
}


async def _get(path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_serves_the_artifact_with_sha256_header_when_version_matches_latest():
    with patch("routers.harness.require_db", return_value=object()), \
         patch("routers.harness.get_latest_release", new=AsyncMock(return_value=_FAKE_RELEASE)), \
         patch("routers.harness.get_release_artifact_bytes", new=AsyncMock(return_value=b"zip-bytes")), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)):
        resp = await _get("/api/harness/download-public/0.8.0")
    assert resp.status_code == 200
    assert resp.content == b"zip-bytes"
    assert resp.headers["x-aurem-package-sha256"] == "deadbeef" * 8
    assert "aurem-loop-0.8.0.zip" in resp.headers["content-disposition"]
    assert resp.headers["content-type"] == "application/zip"


@pytest.mark.asyncio
async def test_404_when_no_release_is_published():
    with patch("routers.harness.require_db", return_value=object()), \
         patch("routers.harness.get_latest_release", new=AsyncMock(return_value=None)), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)):
        resp = await _get("/api/harness/download-public/0.8.0")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_404_never_serves_a_version_other_than_the_actual_latest():
    """A stale pinned copy of the installer script asking for an
    older/mismatched version must fail closed, not silently fetch
    whatever is currently latest instead."""
    with patch("routers.harness.require_db", return_value=object()), \
         patch("routers.harness.get_latest_release", new=AsyncMock(return_value=_FAKE_RELEASE)), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)):
        resp = await _get("/api/harness/download-public/0.7.9")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rate_limited_caller_gets_429_before_any_release_lookup():
    with patch("routers.harness.require_db", return_value=object()), \
         patch("routers.harness.get_latest_release", new=AsyncMock()) as mocked_lookup, \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=False)):
        resp = await _get("/api/harness/download-public/0.8.0")
    assert resp.status_code == 429
    mocked_lookup.assert_not_called()


@pytest.mark.asyncio
async def test_missing_artifact_bytes_returns_503_not_a_crash():
    with patch("routers.harness.require_db", return_value=object()), \
         patch("routers.harness.get_latest_release", new=AsyncMock(return_value=_FAKE_RELEASE)), \
         patch("routers.harness.get_release_artifact_bytes", new=AsyncMock(return_value=None)), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)):
        resp = await _get("/api/harness/download-public/0.8.0")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_no_authorization_header_required():
    """The whole point of this route: the one-line installer never
    sends an Authorization header."""
    with patch("routers.harness.require_db", return_value=object()), \
         patch("routers.harness.get_latest_release", new=AsyncMock(return_value=_FAKE_RELEASE)), \
         patch("routers.harness.get_release_artifact_bytes", new=AsyncMock(return_value=b"zip-bytes")), \
         patch("routers.harness.check_rate_limit_async", new=AsyncMock(return_value=True)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/harness/download-public/0.8.0")  # no headers at all
    assert resp.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
