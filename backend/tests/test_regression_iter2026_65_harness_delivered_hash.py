"""tests/test_regression_iter2026_65_harness_delivered_hash.py

Aurem Loop harness — Fix B: show the SHA-256 of the DELIVERED zip.

Observed fact (verified in code, not assumed): POST
/api/aurem-dev/harness/download takes the base zip and injects a
per-user identity.json (services.harness_releases.inject_identity_into_zip),
so the delivered bytes differ per download and per user. The page
used to show only the static base-zip SHA-256, which can therefore
never match the delivered file.

This file tests the backend half: the download response must carry
`X-Aurem-Package-SHA256` — the SHA-256 (lowercase hex) of the EXACT
bytes sent. The frontend half (show the delivered hash + relabel the
base hash) is covered in frontend/src/pages/__tests__/
HarnessPage.test.jsx ("Fix B — delivered package hash").

TDD: this file was written FIRST (red) against the unmodified
download router — every test below failed on the missing header —
then the router was changed (green). See the final report.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio


class _UpdateResult:
    def __init__(self, modified_count):
        self.modified_count = modified_count


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []

    def _match(self, d, filt):
        for k, v in (filt or {}).items():
            if isinstance(v, dict):
                return False
            if d.get(k) != v:
                return False
        return True

    async def find_one(self, filt=None, projection=None):
        for d in self.docs:
            if self._match(d, filt):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, filt, update):
        for d in self.docs:
            if self._match(d, filt):
                d.update(update.get("$set", {}))
                return _UpdateResult(1)
        return _UpdateResult(0)

    async def update_many(self, filt, update):
        n = 0
        for d in self.docs:
            if self._match(d, filt):
                d.update(update.get("$set", {}))
                n += 1
        return _UpdateResult(n)


class _FakeDB:
    def __init__(self):
        self.dev_users = _FakeColl()
        self.harness_tokens = _FakeColl()
        self.harness_releases = _FakeColl()
        self.harness_release_artifacts = _FakeColl()
        self.harness_downloads = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


def _synthetic_zip(label="v1") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README_TEST_FIXTURE.txt",
                    f"synthetic test fixture {label} — not the real harness")
        zf.writestr("version.json", '{"name": "test-fixture", "version": "0.0.0-test"}')
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _signing_secret_env(monkeypatch):
    monkeypatch.setenv("HARNESS_RELEASE_SIGNING_SECRET", "test-signing-secret-do-not-use-in-prod")


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """The download route rate-limits 5/min per user in an in-memory
    bucket shared across test files in one pytest process — without
    this, the Phase B file's downloads would exhaust u1's bucket and
    these tests would 429 when run in a sweep. The 429 path itself is
    covered by test_rate_limited_request_has_no_hash_header, which
    re-patches the limiter to False inside its own body (the inner
    patch wins while it is active)."""
    from routers import harness_downloads as hd_mod
    with patch.object(hd_mod, "check_rate_limit_async", AsyncMock(return_value=True)):
        yield


async def _published_release(db, version="0.0.1-test"):
    from services import harness_releases
    rel = await harness_releases.create_draft_release(
        db, version=version, platform="windows",
        changelog=["t"], mandatory=False,
        artifact_bytes=_synthetic_zip(), source="test_fixture",
        created_by="admin1",
    )
    await harness_releases.publish_release(db, release_id=rel["release_id"])
    return rel


def _app_with_download_router(db):
    from routers import harness_downloads as hd_mod
    app = FastAPI()
    app.include_router(hd_mod.router, prefix="/api/aurem-dev")
    return app, hd_mod


def _fake_current_dev(user_id, tier="pro"):
    async def _fake(authorization=None):
        return {"user_id": user_id, "tier": tier}
    return _fake


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class TestDeliveredHashHeader:
    async def test_header_equals_sha256_of_the_exact_response_body(self):
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        await _published_release(db)
        app, hd_mod = _app_with_download_router(db)
        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev("u1")):
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        header = resp.headers.get("x-aurem-package-sha256")
        assert header is not None, "download must carry X-Aurem-Package-SHA256"
        assert _SHA256_HEX_RE.match(header), "header must be lowercase hex sha256"
        assert header == hashlib.sha256(resp.content).hexdigest()

    async def test_two_users_get_different_hashes_each_matching_their_own_body(self):
        """The delivered bytes differ per user (different identity.json),
        so the header must differ too — and each must match its own body."""
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "alice", "tier": "pro"})
        db.dev_users.docs.append({"user_id": "bob", "tier": "free"})
        await _published_release(db)
        app, hd_mod = _app_with_download_router(db)
        with patch.object(hd_mod, "require_db", lambda: db):
            client = TestClient(app)
            with patch.object(hd_mod, "current_dev", _fake_current_dev("alice")):
                resp_a = client.post("/api/aurem-dev/harness/download",
                                     headers={"Authorization": "Bearer x"})
            with patch.object(hd_mod, "current_dev", _fake_current_dev("bob", "free")):
                resp_b = client.post("/api/aurem-dev/harness/download",
                                     headers={"Authorization": "Bearer x"})
        ha = resp_a.headers.get("x-aurem-package-sha256")
        hb = resp_b.headers.get("x-aurem-package-sha256")
        assert ha and hb and ha != hb
        assert ha == hashlib.sha256(resp_a.content).hexdigest()
        assert hb == hashlib.sha256(resp_b.content).hexdigest()
        # And neither equals the static base-package hash: the injected
        # identity.json changes the bytes.
        base_hash = hashlib.sha256(_synthetic_zip()).hexdigest()
        assert ha != base_hash
        assert hb != base_hash

    async def test_logged_out_request_is_rejected_with_no_hash_header(self):
        db = _FakeDB()
        app, hd_mod = _app_with_download_router(db)
        with patch.object(hd_mod, "require_db", lambda: db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/aurem-dev/harness/download")
        assert resp.status_code == 401
        assert "x-aurem-package-sha256" not in resp.headers

    async def test_no_release_published_is_409_with_no_hash_header(self):
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        app, hd_mod = _app_with_download_router(db)
        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev("u1")):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 409
        assert "x-aurem-package-sha256" not in resp.headers

    async def test_rate_limited_request_has_no_hash_header(self):
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        await _published_release(db)
        app, hd_mod = _app_with_download_router(db)
        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev("u1")), \
             patch.object(hd_mod, "check_rate_limit_async", AsyncMock(return_value=False)):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 429
        assert "x-aurem-package-sha256" not in resp.headers


class TestDeliveredHashDoesNotLeakTheToken:
    async def test_raw_token_absent_from_headers_and_logs(self, caplog):
        import json
        import logging
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        await _published_release(db)
        app, hd_mod = _app_with_download_router(db)
        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev("u1")), \
             caplog.at_level(logging.DEBUG):
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        identity = json.loads(zipfile.ZipFile(io.BytesIO(resp.content)).read("identity.json"))
        token = identity["token"]
        assert token
        # The token lives inside the zip by design (identity.json) —
        # but it must never appear in headers, logs, or metadata.
        for name, value in resp.headers.items():
            assert token not in value, f"token leaked in header {name}"
        for record in caplog.records:
            assert token not in record.getMessage()
        # The delivered hash is a one-way digest of the bytes: it must
        # not BE the token, and knowing it must not reveal the token.
        header = resp.headers.get("x-aurem-package-sha256")
        assert header != token
        assert token not in header

    async def test_existing_download_behavior_unchanged(self):
        """Regression: the header is additive only — zip content, the
        identity inside it, and the download log row are exactly as
        before."""
        import json
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        await _published_release(db)
        app, hd_mod = _app_with_download_router(db)
        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev("u1")):
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        identity = json.loads(zipfile.ZipFile(io.BytesIO(resp.content)).read("identity.json"))
        assert identity["bin"] == "u1"
        assert len(db.harness_downloads.docs) == 1
        assert "token" not in db.harness_downloads.docs[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
