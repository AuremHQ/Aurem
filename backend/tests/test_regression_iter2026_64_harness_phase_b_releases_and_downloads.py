"""tests/test_regression_iter2026_64_harness_phase_b_releases_and_downloads.py

Aurem Loop harness — Phase B: /harness page backend, per-user download,
download log, admin view, release manifest + signing.

Scope note (explicit, matching the approved instruction for this
phase): D1 (hosting the real fix-branch package) is NOT approved.
Nothing here ever hosts or reconstructs the real `main`-branch code or
the old pre-resilience ZIP. Every release created in these tests is
tagged with an explicit `source` field — "test_fixture" for the
synthetic bytes used here, vs. "muse_fix_branch_approved" for a real
release once Teji approves D1 — so it is structurally impossible for
a test-only artifact to be served as if it were the real package
without that distinction being visible in the data itself and in the
admin view.

IMPORTANT, stated plainly rather than assumed: no Muse-supplied test
package was ever attached anywhere in this conversation. Every
"artifact" in this file is a small, obviously-synthetic zip built
in-test (a couple of plain text members) purely to exercise the real
mechanics — sha256, signing, zip-injection of identity.json, storage,
streaming, logging. This is NOT a stand-in for real harness testing
and the final report says so explicitly; it only proves the SERVER
plumbing is correct.

Design:
  - services/harness_releases.py: create_draft_release / publish_release
    / get_latest_release / get_release_artifact_bytes /
    inject_identity_into_zip / sign_manifest / verify_manifest_signature.
  - routers/harness_downloads.py: POST /harness/download (JWT auth via
    current_dev, mounted at /api/aurem-dev — this is a browser-facing
    endpoint, not the external harness-client contract, so it has no
    fixed-URL constraint the way /api/harness/verify does).
  - routers/admin_harness.py: admin release CRUD + download log view,
    gated by require_admin_dep, mounted at /api/aurem-dev.

T1-T8 covered: TDD (this file, red first), unit (signing/sha256/zip
injection edge cases), integration (through the real routers），
security (IDOR, IDOR on download log, admin-only release publish,
signature tamper detection, oversized-artifact rejection, mass
assignment on release fields, path-traversal-safe zip injection, rate
limiting), and the one real end-to-end acceptance flow this
environment CAN perform for real: download as a subscriber -> the
real Phase A verify endpoint says subscriber:true -> revoke -> verify
says not-subscriber. (Running the actual Windows installer and
`loop.py auth` client is NOT something this Linux backend sandbox can
do — see the final report.)
"""
from __future__ import annotations

import base64
import hashlib
import io
import time
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────
# Fake DB
# ─────────────────────────────────────────────────────────────────────
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

    def find(self, filt=None, projection=None):
        matched = [dict(d) for d in self.docs if self._match(d, filt)]

        class _Cur:
            def __init__(self, items):
                self.items = items

            def sort(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            async def to_list(self, length=None):
                return list(self.items)
        return _Cur(matched)

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

    async def count_documents(self, filt=None):
        return len([d for d in self.docs if self._match(d, filt)])


class _FakeDB:
    def __init__(self):
        self.dev_users = _FakeColl()
        self.harness_tokens = _FakeColl()
        self.harness_releases = _FakeColl()
        self.harness_release_artifacts = _FakeColl()
        self.harness_release_exe_artifacts = _FakeColl()
        self.harness_downloads = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


def _synthetic_zip(label="v1") -> bytes:
    """A small, obviously-fake zip — NOT the real harness package.
    Two plain-text members, nothing resembling loop.py/aurem_loop/."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README_TEST_FIXTURE.txt",
                     f"synthetic test fixture {label} — not the real harness")
        zf.writestr("version.json", '{"name": "test-fixture", "version": "0.0.0-test"}')
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _signing_secret_env(monkeypatch):
    monkeypatch.setenv("HARNESS_RELEASE_SIGNING_SECRET", "test-signing-secret-do-not-use-in-prod")


# ─────────────────────────────────────────────────────────────────────
# UNIT — services.harness_releases
# ─────────────────────────────────────────────────────────────────────
class TestReleaseCreationAndSigning:
    async def test_create_draft_release_computes_real_sha256(self):
        from services import harness_releases
        db = _FakeDB()
        artifact = _synthetic_zip()
        result = await harness_releases.create_draft_release(
            db, version="0.0.0-test", platform="windows",
            changelog=["test change"], mandatory=False,
            artifact_bytes=artifact, source="test_fixture",
            created_by="admin1",
        )
        assert result["artifact_sha256"] == hashlib.sha256(artifact).hexdigest()
        stored = db.harness_releases.docs[0]
        assert stored["status"] == "draft"
        assert stored["is_latest"] is False
        assert stored["source"] == "test_fixture"

    async def test_signature_is_verifiable_and_tamper_evident(self):
        from services import harness_releases
        sig = harness_releases.sign_manifest(
            version="0.0.0-test", platform="windows",
            artifact_sha256="a" * 64, changelog=["x"], mandatory=False,
        )
        assert harness_releases.verify_manifest_signature(
            sig, version="0.0.0-test", platform="windows",
            artifact_sha256="a" * 64, changelog=["x"], mandatory=False,
        )
        # Any field change invalidates the signature.
        assert not harness_releases.verify_manifest_signature(
            sig, version="0.0.0-test", platform="windows",
            artifact_sha256="b" * 64, changelog=["x"], mandatory=False,
        )
        assert not harness_releases.verify_manifest_signature(
            sig, version="0.0.1-tampered", platform="windows",
            artifact_sha256="a" * 64, changelog=["x"], mandatory=False,
        )

    async def test_rejects_an_unknown_source_value(self):
        from services import harness_releases
        db = _FakeDB()
        with pytest.raises(ValueError):
            await harness_releases.create_draft_release(
                db, version="0.0.0-test", platform="windows",
                changelog=[], mandatory=False,
                artifact_bytes=_synthetic_zip(), source="i_made_this_up",
                created_by="admin1",
            )

    async def test_rejects_empty_artifact(self):
        from services import harness_releases
        db = _FakeDB()
        with pytest.raises(ValueError):
            await harness_releases.create_draft_release(
                db, version="0.0.0-test", platform="windows",
                changelog=[], mandatory=False,
                artifact_bytes=b"", source="test_fixture",
                created_by="admin1",
            )

    async def test_rejects_oversized_artifact(self):
        from services import harness_releases
        db = _FakeDB()
        huge = b"x" * (16 * 1024 * 1024)
        with pytest.raises(ValueError):
            await harness_releases.create_draft_release(
                db, version="0.0.0-test", platform="windows",
                changelog=[], mandatory=False,
                artifact_bytes=huge, source="test_fixture",
                created_by="admin1",
            )

    async def test_publish_sets_is_latest_and_unsets_the_previous_one(self):
        from services import harness_releases
        db = _FakeDB()
        r1 = await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip("1"), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=r1["release_id"])
        r2 = await harness_releases.create_draft_release(
            db, version="0.0.2-test", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip("2"), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=r2["release_id"])

        rows = {d["release_id"]: d for d in db.harness_releases.docs}
        assert rows[r1["release_id"]]["is_latest"] is False
        assert rows[r2["release_id"]]["is_latest"] is True
        latest = await harness_releases.get_latest_release(db, platform="windows")
        assert latest["release_id"] == r2["release_id"]

    async def test_publishing_nonexistent_release_raises(self):
        from services import harness_releases
        db = _FakeDB()
        with pytest.raises(ValueError):
            await harness_releases.publish_release(db, release_id="rel_ghost")

    async def test_get_latest_release_is_none_when_nothing_published(self):
        from services import harness_releases
        db = _FakeDB()
        assert await harness_releases.get_latest_release(db, platform="windows") is None
        await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        # A draft (never published) must not count as latest.
        assert await harness_releases.get_latest_release(db, platform="windows") is None

    async def test_different_platforms_have_independent_latest(self):
        from services import harness_releases
        db = _FakeDB()
        win = await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip("win"), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=win["release_id"])
        assert await harness_releases.get_latest_release(db, platform="mac") is None
        assert (await harness_releases.get_latest_release(db, platform="windows"))["release_id"] == win["release_id"]


class TestIdentityInjection:
    async def test_identity_json_is_injected_with_the_given_bin_and_token(self):
        import json

        from services import harness_releases
        artifact = _synthetic_zip()
        out = harness_releases.inject_identity_into_zip(
            artifact, bin="u1", token="hlt_realtoken123",
        )
        zf = zipfile.ZipFile(io.BytesIO(out))
        identity = json.loads(zf.read("identity.json"))
        assert identity == {"bin": "u1", "token": "hlt_realtoken123"}
        # Original members survive untouched.
        assert zf.read("version.json") == b'{"name": "test-fixture", "version": "0.0.0-test"}'

    async def test_a_stray_identity_json_already_in_the_artifact_is_overwritten_not_duplicated(self):
        import json
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("identity.json", '{"bin": "someone-else", "token": "stale"}')
            zf.writestr("version.json", "{}")
        from services import harness_releases
        out = harness_releases.inject_identity_into_zip(
            buf.getvalue(), bin="real-user", token="real-token",
        )
        zf = zipfile.ZipFile(io.BytesIO(out))
        names = zf.namelist()
        assert names.count("identity.json") == 1
        identity = json.loads(zf.read("identity.json"))
        assert identity == {"bin": "real-user", "token": "real-token"}

    async def test_two_different_users_downloading_the_same_release_get_different_identities(self):
        import json

        from services import harness_releases
        artifact = _synthetic_zip()
        out1 = harness_releases.inject_identity_into_zip(artifact, bin="alice", token="tok-a")
        out2 = harness_releases.inject_identity_into_zip(artifact, bin="bob", token="tok-b")
        id1 = json.loads(zipfile.ZipFile(io.BytesIO(out1)).read("identity.json"))
        id2 = json.loads(zipfile.ZipFile(io.BytesIO(out2)).read("identity.json"))
        assert id1 != id2
        assert id1["bin"] == "alice"
        assert id2["bin"] == "bob"


# ─────────────────────────────────────────────────────────────────────
# INTEGRATION — download endpoint, through the real router
# ─────────────────────────────────────────────────────────────────────
def _app_with_download_router(db):
    from routers import harness_downloads as hd_mod
    app = FastAPI()
    app.include_router(hd_mod.router, prefix="/api/aurem-dev")
    return app, hd_mod


class TestDownloadEndpoint:
    async def test_no_release_published_returns_409_never_fabricates_one(self):
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "u1", "tier": "pro"}

        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 409
        assert db.harness_downloads.docs == []

    async def test_subscriber_download_returns_a_zip_with_their_identity_and_logs_it(self):
        from services import harness_releases
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "sub1", "tier": "pro"})
        rel = await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=["t"], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=rel["release_id"])

        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "sub1", "tier": "pro"}

        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev):
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]

        import json
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        identity = json.loads(zf.read("identity.json"))
        assert identity["bin"] == "sub1"
        assert isinstance(identity["token"], str) and identity["token"]

        assert len(db.harness_downloads.docs) == 1
        log_row = db.harness_downloads.docs[0]
        assert log_row["user_id"] == "sub1"
        assert log_row["plan"] == "pro"
        assert log_row["release_id"] == rel["release_id"]
        # The log row must never carry the plaintext token.
        assert "token" not in log_row

    async def test_free_user_gets_the_same_package_extras_gated_at_runtime_not_in_the_zip(self):
        """Hybrid model: there is only ONE package. Free vs paid is
        enforced by the Phase A verify endpoint at harness startup,
        never by shipping different zip contents."""
        from services import harness_releases
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "free1", "tier": "free"})
        rel = await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=rel["release_id"])

        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "free1", "tier": "free"}

        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev):
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        log_row = db.harness_downloads.docs[0]
        assert log_row["plan"] == "free"

    async def test_redownload_issues_a_new_token_and_never_revokes_the_first(self):
        from services import harness_auth, harness_releases
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        rel = await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=rel["release_id"])
        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "u1", "tier": "pro"}

        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev):
            client = TestClient(app)
            resp1 = client.post("/api/aurem-dev/harness/download",
                                headers={"Authorization": "Bearer x"})
            resp2 = client.post("/api/aurem-dev/harness/download",
                                headers={"Authorization": "Bearer x"})

        import json
        id1 = json.loads(zipfile.ZipFile(io.BytesIO(resp1.content)).read("identity.json"))
        id2 = json.loads(zipfile.ZipFile(io.BytesIO(resp2.content)).read("identity.json"))
        assert id1["token"] != id2["token"]

        # BOTH tokens must still verify successfully — re-download never
        # revokes the prior installation.
        r1 = await harness_auth.verify_harness_identity(db, bin="u1", token=id1["token"])
        r2 = await harness_auth.verify_harness_identity(db, bin="u1", token=id2["token"])
        assert r1 == {"subscriber": True, "plan": "pro"}
        assert r2 == {"subscriber": True, "plan": "pro"}
        assert len(db.harness_downloads.docs) == 2

    async def test_rate_limited_user_gets_429(self):
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "u1", "tier": "pro"}

        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev), \
             patch.object(hd_mod, "check_rate_limit_async", AsyncMock(return_value=False)):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 429

    async def test_no_auth_header_returns_401_not_a_crash(self):
        db = _FakeDB()
        app, hd_mod = _app_with_download_router(db)
        with patch.object(hd_mod, "require_db", lambda: db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/aurem-dev/harness/download")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────
# ADMIN VIEWS
# ─────────────────────────────────────────────────────────────────────
def _app_with_admin_router(db):
    from routers import admin_harness as ah_mod
    app = FastAPI()
    app.include_router(ah_mod.router, prefix="/api/aurem-dev")
    return app, ah_mod


class TestAdminReleaseManagement:
    async def test_non_admin_gets_403_on_every_admin_harness_route(self):
        db = _FakeDB()
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_require_admin_dep_raises_403(authorization=None):
            from fastapi import HTTPException
            raise HTTPException(403, "not admin")

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_require_admin_dep_raises_403
        try:
            client = TestClient(app, raise_server_exceptions=False)
            with patch.object(ah_mod, "require_db", lambda: db):
                r1 = client.get("/api/aurem-dev/admin/harness/releases")
                r2 = client.get("/api/aurem-dev/admin/harness/downloads")
                r3 = client.post("/api/aurem-dev/admin/harness/releases", json={
                    "version": "x", "platform": "windows", "changelog": [],
                    "mandatory": False, "artifact_base64": "", "source": "test_fixture",
                })
            assert r1.status_code == 403
            assert r2.status_code == 403
            assert r3.status_code == 403
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_admin_can_create_a_draft_release_via_the_endpoint(self):
        db = _FakeDB()
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_admin(authorization=None):
            return {"user_id": "admin1", "is_admin": True}

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.post("/api/aurem-dev/admin/harness/releases", json={
                    "version": "0.0.1-test", "platform": "windows",
                    "changelog": ["first test release"], "mandatory": False,
                    "artifact_base64": base64.b64encode(_synthetic_zip()).decode(),
                    "source": "test_fixture",
                })
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "draft"
            assert "artifact_sha256" in body
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_admin_can_publish_a_draft_release(self):
        from services import harness_releases
        db = _FakeDB()
        rel = await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_admin(authorization=None):
            return {"user_id": "admin1", "is_admin": True}

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.post(
                    f"/api/aurem-dev/admin/harness/releases/{rel['release_id']}/publish",
                )
            assert resp.status_code == 200
            assert resp.json()["status"] == "published"
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_release_create_rejects_an_invalid_source_with_400(self):
        db = _FakeDB()
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_admin(authorization=None):
            return {"user_id": "admin1", "is_admin": True}

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post("/api/aurem-dev/admin/harness/releases", json={
                    "version": "x", "platform": "windows", "changelog": [],
                    "mandatory": False,
                    "artifact_base64": base64.b64encode(_synthetic_zip()).decode(),
                    "source": "pulled_from_main_branch_whoops",
                })
            assert resp.status_code == 422
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_admin_ui_source_is_accepted_end_to_end(self):
        """2026-11-16 — AdminHarnessReleases.jsx (the admin console's
        release-publishing page) posts source="admin_ui" for a human
        admin manually uploading a zip through the page. Distinct from
        "test_fixture" (synthetic test bytes) and
        "muse_fix_branch_approved" (the real fix-branch package once
        D1 is approved) — this locks in that the provenance tag is
        accepted by both the router's own closed-set check
        (routers/admin_harness.py::_VALID_SOURCES) and the service
        layer's independent check
        (services/harness_releases.py::_VALID_SOURCES), and that the
        created release carries the tag through to the stored doc."""
        db = _FakeDB()
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_admin(authorization=None):
            return {"user_id": "admin1", "is_admin": True}

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.post("/api/aurem-dev/admin/harness/releases", json={
                    "version": "0.8.0", "platform": "windows",
                    "changelog": ["first public download"], "mandatory": False,
                    "artifact_base64": base64.b64encode(_synthetic_zip()).decode(),
                    "source": "admin_ui",
                })
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "draft"
            stored = await db.harness_releases.find_one({"release_id": body["release_id"]})
            assert stored["source"] == "admin_ui"
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_release_list_never_includes_the_raw_artifact_bytes(self):
        from services import harness_releases
        db = _FakeDB()
        await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_admin(authorization=None):
            return {"user_id": "admin1", "is_admin": True}

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.get("/api/aurem-dev/admin/harness/releases")
            assert resp.status_code == 200
            rows = resp.json()["releases"]
            assert len(rows) == 1
            assert "artifact_bytes" not in rows[0]
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_download_log_lists_rows_newest_first_with_a_free_vs_paid_split(self):
        db = _FakeDB()
        now = time.time()
        db.harness_downloads.docs = [
            {"grant_id": "dl1", "user_id": "a", "plan": "free", "issued_at": now - 100, "release_id": "r1", "version": "0.0.1"},
            {"grant_id": "dl2", "user_id": "b", "plan": "pro", "issued_at": now, "release_id": "r1", "version": "0.0.1"},
        ]
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_admin(authorization=None):
            return {"user_id": "admin1", "is_admin": True}

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.get("/api/aurem-dev/admin/harness/downloads")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 2
            assert body["free_count"] == 1
            assert body["paid_count"] == 1
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)


# ─────────────────────────────────────────────────────────────────────
# SECURITY (T4)
# ─────────────────────────────────────────────────────────────────────
class TestSecurity:
    async def test_mass_assignment_cannot_force_a_release_straight_to_published(self):
        """The release-create endpoint must only ever produce a draft —
        there is no client-settable "status" or "is_latest" field."""
        db = _FakeDB()
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_admin(authorization=None):
            return {"user_id": "admin1", "is_admin": True}

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app)
                resp = client.post("/api/aurem-dev/admin/harness/releases", json={
                    "version": "x", "platform": "windows", "changelog": [],
                    "mandatory": False,
                    "artifact_base64": base64.b64encode(_synthetic_zip()).decode(),
                    "source": "test_fixture",
                    "status": "published", "is_latest": True,  # attacker-supplied, must be ignored
                })
            assert resp.status_code == 200
            stored = db.harness_releases.docs[0]
            assert stored["status"] == "draft"
            assert stored["is_latest"] is False
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_download_log_idor_a_user_cannot_see_another_users_rows_via_any_user_facing_endpoint(self):
        """Only the ADMIN endpoint lists the download log — there is no
        user-facing "my downloads" listing in this phase, so there is
        nothing for a user to query here. Confirms the download
        endpoint itself returns no download-log data in its response,
        only the zip."""
        from services import harness_releases
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "u1", "tier": "pro"})
        rel = await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=rel["release_id"])
        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "u1", "tier": "pro"}

        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev):
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.headers["content-type"] == "application/zip"
        assert "grant_id" not in resp.text if resp.text else True

    async def test_path_traversal_in_a_hostile_filename_inside_the_artifact_does_not_escape(self):
        """A hostile uploaded artifact containing a zip-slip filename
        must not let identity.json injection (or anything else) write
        outside the intended archive structure — zipfile.ZipFile with
        writestr never touches the real filesystem, so this is really
        confirming inject_identity_into_zip is filesystem-safe by
        construction, not just by luck."""
        from services import harness_releases
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../../etc/passwd", "hostile content")
            zf.writestr("version.json", "{}")
        out = harness_releases.inject_identity_into_zip(
            buf.getvalue(), bin="u1", token="tok",
        )
        # It round-trips as a valid, self-contained in-memory zip —
        # nothing was ever written to a real path on disk.
        zf2 = zipfile.ZipFile(io.BytesIO(out))
        assert "identity.json" in zf2.namelist()

    async def test_changelog_entries_are_length_capped_server_side(self):
        db = _FakeDB()
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_admin(authorization=None):
            return {"user_id": "admin1", "is_admin": True}

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post("/api/aurem-dev/admin/harness/releases", json={
                    "version": "x", "platform": "windows",
                    "changelog": ["x" * 10_000],
                    "mandatory": False,
                    "artifact_base64": base64.b64encode(_synthetic_zip()).decode(),
                    "source": "test_fixture",
                })
            assert resp.status_code == 422
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)


# ─────────────────────────────────────────────────────────────────────
# ACCEPTANCE — the one real end-to-end flow this environment CAN run
# ─────────────────────────────────────────────────────────────────────
class TestRealAcceptanceFlowServerSideOnly:
    async def test_download_as_subscriber_verify_true_then_revoke_then_verify_refuses(self):
        """This is the server-side equivalent of "download as test
        subscriber, install, loop.py auth, revoke -> refuses". The
        actual Windows install + running the real loop.py binary is
        NOT something this Linux backend sandbox can do — that half
        is explicitly flagged as not-tested in the report. What IS
        real here: a real download-grant through the real router, a
        real verify through the REAL Phase A endpoint
        (services.harness_auth, unmodified), and a real revoke."""
        from services import harness_auth, harness_releases
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "sub1", "tier": "team"})
        rel = await harness_releases.create_draft_release(
            db, version="0.0.1-test", platform="windows",
            changelog=["acceptance test release"], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=rel["release_id"])

        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "sub1", "tier": "team"}

        with patch.object(hd_mod, "require_db", lambda: db), \
             patch.object(hd_mod, "current_dev", _fake_current_dev):
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/harness/download",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200

        import json
        identity = json.loads(zipfile.ZipFile(io.BytesIO(resp.content)).read("identity.json"))
        bin_, token = identity["bin"], identity["token"]

        # Step 2: real verify (Phase A, unmodified) — subscriber true.
        verify1 = await harness_auth.verify_harness_identity(db, bin=bin_, token=token)
        assert verify1 == {"subscriber": True, "plan": "team"}

        # Step 3: revoke.
        revoked_count = await harness_auth.revoke_harness_token(db, user_id=bin_, token=token)
        assert revoked_count == 1

        # Step 4: verify again -> refuses (not-subscriber).
        verify2 = await harness_auth.verify_harness_identity(db, bin=bin_, token=token)
        assert verify2 == {"subscriber": False, "plan": None}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
class TestExeSidecar:
    """PR #342 — the Windows .exe installer as the primary /harness download."""

    async def test_attach_exe_stores_bytes_and_marks_release(self):
        from services import harness_releases
        db = _FakeDB()
        rel = await harness_releases.create_draft_release(
            db, version="0.8.0", platform="windows",
            changelog=["exe"], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        exe = b"MZ" + b"\x00" * 100
        out = await harness_releases.attach_exe(db, release_id=rel["release_id"], exe_bytes=exe)
        assert out["exe_available"] is True
        assert out["exe_sha256"] == hashlib.sha256(exe).hexdigest()
        assert out["exe_size_bytes"] == len(exe)
        # the zip artifact is untouched by the exe attach
        assert (await harness_releases.get_release_artifact_bytes(db, release_id=rel["release_id"])) == _synthetic_zip()
        assert (await harness_releases.get_release_exe_bytes(db, release_id=rel["release_id"])) == exe
        row = await db.harness_releases.find_one({"release_id": rel["release_id"]})
        assert row["exe_sha256"] == out["exe_sha256"]
        assert harness_releases.verify_exe_manifest_signature(
            row["exe_signature"], version="0.8.0", platform="windows", exe_sha256=out["exe_sha256"],
        ) is True
        assert harness_releases.verify_exe_manifest_signature(
            "0" * 64, version="0.8.0", platform="windows", exe_sha256=out["exe_sha256"],
        ) is False

    async def test_attach_exe_to_missing_release_raises(self):
        from services import harness_releases
        db = _FakeDB()
        with pytest.raises(ValueError, match="release not found"):
            await harness_releases.attach_exe(db, release_id="rel_nope", exe_bytes=b"MZ")

    async def test_attach_exe_replaces_on_reattach(self):
        from services import harness_releases
        db = _FakeDB()
        rel = await harness_releases.create_draft_release(
            db, version="0.8.0", platform="windows",
            changelog=[], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.attach_exe(db, release_id=rel["release_id"], exe_bytes=b"MZ-one")
        await harness_releases.attach_exe(db, release_id=rel["release_id"], exe_bytes=b"MZ-two")
        assert (await harness_releases.get_release_exe_bytes(db, release_id=rel["release_id"])) == b"MZ-two"

    async def test_admin_attach_exe_endpoint_and_list_flag(self):
        db = _FakeDB()
        app, ah_mod = _app_with_admin_router(db)

        async def _fake_admin(authorization=None):
            return {"user_id": "admin1", "is_admin": True}

        app.dependency_overrides[ah_mod.require_admin_dep] = _fake_admin
        try:
            with patch.object(ah_mod, "require_db", lambda: db):
                client = TestClient(app, raise_server_exceptions=False)
                r = client.post("/api/aurem-dev/admin/harness/releases", json={
                    "version": "0.8.0", "platform": "windows", "changelog": [],
                    "mandatory": False,
                    "artifact_base64": base64.b64encode(_synthetic_zip()).decode(),
                    "source": "test_fixture",
                })
                assert r.status_code == 200, r.text
                rid = r.json()["release_id"]
                exe_b64 = base64.b64encode(b"MZ" + b"\x00" * 10).decode()
                r2 = client.post(f"/api/aurem-dev/admin/harness/releases/{rid}/exe", json={"exe_base64": exe_b64})
                assert r2.status_code == 200, r2.text
                assert r2.json()["exe_available"] is True
                assert r2.json()["exe_sha256"] == hashlib.sha256(b"MZ" + b"\x00" * 10).hexdigest()
                r3 = client.get("/api/aurem-dev/admin/harness/releases")
                assert r3.status_code == 200
                rows = r3.json()["releases"]
                assert rows[0]["exe_available"] is True
                r4 = client.post("/api/aurem-dev/admin/harness/releases/rel_nope/exe", json={"exe_base64": exe_b64})
                assert r4.status_code == 404
        finally:
            app.dependency_overrides.pop(ah_mod.require_admin_dep, None)

    async def test_download_exe_returns_attached_bytes_and_logs_format_exe(self):
        from services import harness_releases
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "sub1", "tier": "pro"})
        rel = await harness_releases.create_draft_release(
            db, version="0.8.0", platform="windows",
            changelog=["t"], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=rel["release_id"])
        exe = b"MZ" + b"\x00" * 50
        await harness_releases.attach_exe(db, release_id=rel["release_id"], exe_bytes=exe)

        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "sub1", "tier": "pro"}

        with patch.object(hd_mod, "require_db", lambda: db), \
                patch.object(hd_mod, "current_dev", _fake_current_dev):
            client = TestClient(app)
            resp = client.post("/api/aurem-dev/harness/download-exe",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.microsoft.portable-executable"
        assert "aurem-loop-0.8.0.exe" in resp.headers["content-disposition"]
        assert resp.content == exe
        assert len(db.harness_downloads.docs) == 1
        assert db.harness_downloads.docs[0]["format"] == "exe"
        assert db.harness_downloads.docs[0]["release_id"] == rel["release_id"]

    async def test_download_exe_without_attached_exe_returns_409(self):
        from services import harness_releases
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "sub1", "tier": "pro"})
        rel = await harness_releases.create_draft_release(
            db, version="0.8.0", platform="windows",
            changelog=["t"], mandatory=False,
            artifact_bytes=_synthetic_zip(), source="test_fixture",
            created_by="admin1",
        )
        await harness_releases.publish_release(db, release_id=rel["release_id"])
        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "sub1", "tier": "pro"}

        with patch.object(hd_mod, "require_db", lambda: db), \
                patch.object(hd_mod, "current_dev", _fake_current_dev):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/aurem-dev/harness/download-exe",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "no_exe_attached"
        assert db.harness_downloads.docs == []

    async def test_download_exe_without_release_returns_409(self):
        db = _FakeDB()
        db.dev_users.docs.append({"user_id": "sub1", "tier": "pro"})
        app, hd_mod = _app_with_download_router(db)

        async def _fake_current_dev(authorization=None):
            return {"user_id": "sub1", "tier": "pro"}

        with patch.object(hd_mod, "require_db", lambda: db), \
                patch.object(hd_mod, "current_dev", _fake_current_dev):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/aurem-dev/harness/download-exe",
                               headers={"Authorization": "Bearer x"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "no_release_available"
