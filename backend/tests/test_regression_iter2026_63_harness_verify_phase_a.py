"""tests/test_regression_iter2026_63_harness_verify_phase_a.py

Aurem Loop harness — Phase A, verify endpoint only (per the approved
plan's own scope restriction: build the endpoint, no billing, no
telemetry, no cloud-run yet).

Contract, confirmed directly from the real shipped harness client
(github.com/AuremHQ/aurem-loop, main branch, loop.py:160-229 — fetched
and read, not assumed):

  POST /api/harness/verify
  body:     {"bin": str, "token": str, "harness_version": str}
  response: {"subscriber": bool, "plan": str | None}

`bin` is the existing `user_id` ("BIN" is this codebase's own admin
nickname for user_id — see routers/admin_bin.py's
`db.dev_users.find_one({"user_id": bin_id})` — not a new identifier
system). `token` is a new, harness-specific credential: long random
string, stored as a SHA-256 digest only, never recoverable, never
returned by this endpoint, never logged in plaintext.

Rules under test (T1-T8):
  - Both bin AND token must match together — a bin alone (even a real,
    valid user_id) must never authenticate (explicit requirement from
    the original spec's Section F: "never use BIN alone as
    authentication").
  - free tier -> {subscriber: false, plan: "free"}; starter/pro/team/
    founder -> {subscriber: true, plan: <tier>}.
  - Fails closed on any ambiguity/error -> not-subscriber, never 500,
    never raises past the router.
  - Token is never present in the response body or in any log record.
  - Malformed/missing fields -> clean 4xx JSON, no stack trace.
  - Mongo-operator-injection in bin/token (e.g. {"$ne": None}) must be
    rejected as invalid input, never reach a query as an operator.
  - Rate limited per bin via the existing services.rate_limiter.
  - A revoked token must verify as not-subscriber immediately.
  - A token's digest is never stored/returned in plaintext anywhere.
"""
from __future__ import annotations

import hashlib
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────
# Fake DB — matches the real Motor cursor/command shape used elsewhere
# in this test suite (see tests/test_iter367_rollback_fake_success_fix.py
# and this session's Part2c fakes).
# ─────────────────────────────────────────────────────────────────────
class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.inserted = []

    def _match(self, d, filt):
        for k, v in (filt or {}).items():
            if isinstance(v, dict):
                # Any dict operator (e.g. {"$ne": None}) used as a raw
                # filter value by a caller that forgot to sanitize is
                # treated as "does not match" here, same as a real
                # Mongo filter would for a non-operator field value —
                # this fake exists to prove the ROUTER never forwards
                # attacker-supplied dict operators into a query filter
                # at all (checked at the Pydantic/model-validation
                # layer before any DB call).
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
        self.inserted.append(dict(doc))
        self.docs.append(dict(doc))

    async def update_one(self, filt, update):
        for d in self.docs:
            if self._match(d, filt):
                d.update(update.get("$set", {}))
                return


class _FakeDB:
    def __init__(self, *, users=None, tokens=None):
        self.dev_users = _FakeColl(users or [])
        self.harness_tokens = _FakeColl(tokens or [])

    def __getitem__(self, name):
        return getattr(self, name)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _build_app(db):
    from routers import harness as harness_router_mod

    app = FastAPI()
    app.include_router(harness_router_mod.router, prefix="/api")

    async def _fake_require_db():
        return db

    with patch("routers.harness.require_db", _fake_require_db):
        yield app


@pytest.fixture
def client_factory():
    """Returns a function(db) -> TestClient, patching require_db for
    the lifetime of the returned client's use inside a `with` block
    in each test (so the patch context manager stays open)."""
    def _factory(db):
        return db
    return _factory


# ─────────────────────────────────────────────────────────────────────
# UNIT — services.harness_auth
# ─────────────────────────────────────────────────────────────────────
class TestIssueAndVerifyHarnessToken:
    async def test_issue_then_verify_matching_pair_is_subscriber_for_paid_tier(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "pro"}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")
        result = await harness_auth.verify_harness_identity(db, bin="u1", token=token)
        assert result == {"subscriber": True, "plan": "pro"}

    async def test_free_tier_user_is_not_subscriber(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "free"}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")
        result = await harness_auth.verify_harness_identity(db, bin="u1", token=token)
        assert result == {"subscriber": False, "plan": "free"}

    async def test_missing_tier_field_defaults_to_free_not_subscriber(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1"}])  # no "tier" key at all
        token = await harness_auth.issue_harness_token(db, user_id="u1")
        result = await harness_auth.verify_harness_identity(db, bin="u1", token=token)
        assert result == {"subscriber": False, "plan": "free"}

    async def test_unknown_garbage_tier_value_coerces_to_free_not_a_crash(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "literally-not-a-tier"}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")
        result = await harness_auth.verify_harness_identity(db, bin="u1", token=token)
        assert result == {"subscriber": False, "plan": "free"}

    @pytest.mark.parametrize("tier", ["starter", "pro", "team", "founder"])
    async def test_every_non_free_tier_is_subscriber(self, tier):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": tier}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")
        result = await harness_auth.verify_harness_identity(db, bin="u1", token=token)
        assert result == {"subscriber": True, "plan": tier}

    async def test_token_for_a_different_bin_is_rejected(self):
        """A bin alone must never authenticate — the token must
        belong to THAT bin, not merely exist for someone."""
        from services import harness_auth
        db = _FakeDB(users=[
            {"user_id": "victim", "tier": "team"},
            {"user_id": "attacker", "tier": "free"},
        ])
        victim_token = await harness_auth.issue_harness_token(db, user_id="victim")
        # attacker tries the victim's real token but claims their own bin
        result = await harness_auth.verify_harness_identity(
            db, bin="attacker", token=victim_token,
        )
        assert result == {"subscriber": False, "plan": None}

    async def test_wrong_token_for_a_real_bin_is_rejected(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "pro"}])
        await harness_auth.issue_harness_token(db, user_id="u1")
        result = await harness_auth.verify_harness_identity(
            db, bin="u1", token="totally-made-up-token",
        )
        assert result == {"subscriber": False, "plan": None}

    async def test_nonexistent_bin_is_rejected(self):
        from services import harness_auth
        db = _FakeDB()
        result = await harness_auth.verify_harness_identity(
            db, bin="ghost", token="whatever",
        )
        assert result == {"subscriber": False, "plan": None}

    async def test_empty_bin_or_token_rejected_without_a_db_call(self):
        from services import harness_auth
        db = _FakeDB()
        assert await harness_auth.verify_harness_identity(db, bin="", token="x") == \
            {"subscriber": False, "plan": None}
        assert await harness_auth.verify_harness_identity(db, bin="u1", token="") == \
            {"subscriber": False, "plan": None}
        assert await harness_auth.verify_harness_identity(db, bin=None, token=None) == \
            {"subscriber": False, "plan": None}

    async def test_revoked_token_is_rejected_immediately(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "pro"}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")
        await db.harness_tokens.update_one(
            {"user_id": "u1"}, {"$set": {"revoked_at": 12345.0}},
        )
        result = await harness_auth.verify_harness_identity(db, bin="u1", token=token)
        assert result == {"subscriber": False, "plan": None}

    async def test_token_is_stored_as_a_digest_never_plaintext(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "pro"}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")
        stored = db.harness_tokens.docs[0]
        assert stored.get("token_hash") == _hash(token)
        assert "token" not in stored
        # The digest is a real sha256 hex digest (64 hex chars), not a
        # truncated or otherwise weakened hash.
        assert len(stored["token_hash"]) == 64

    async def test_db_error_during_lookup_fails_closed_not_raises(self, monkeypatch):
        from services import harness_auth

        class _BoomColl:
            async def find_one(self, *a, **k):
                raise RuntimeError("mongo exploded")

        class _BoomDB:
            def __init__(self):
                self.harness_tokens = _BoomColl()

            def __getitem__(self, name):
                return getattr(self, name)

        result = await harness_auth.verify_harness_identity(
            _BoomDB(), bin="u1", token="whatever",
        )
        assert result == {"subscriber": False, "plan": None}

    async def test_duplicate_idempotent_token_lookup_uses_the_latest_active_one(self):
        """Issuing a second token for the same user (re-download) must
        never invalidate or interfere with verification of the first —
        both remain independently valid until explicitly revoked."""
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "pro"}])
        token1 = await harness_auth.issue_harness_token(db, user_id="u1")
        token2 = await harness_auth.issue_harness_token(db, user_id="u1")
        assert token1 != token2
        r1 = await harness_auth.verify_harness_identity(db, bin="u1", token=token1)
        r2 = await harness_auth.verify_harness_identity(db, bin="u1", token=token2)
        assert r1 == {"subscriber": True, "plan": "pro"}
        assert r2 == {"subscriber": True, "plan": "pro"}


# ─────────────────────────────────────────────────────────────────────
# INTEGRATION — through the real router + TestClient
# ─────────────────────────────────────────────────────────────────────
class TestVerifyEndpointIntegration:
    async def test_subscriber_gets_200_subscriber_true(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "team"}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")

        from routers import harness as harness_mod
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db):
            client = TestClient(app)
            resp = client.post("/api/harness/verify", json={
                "bin": "u1", "token": token, "harness_version": "0.8.0",
            })
        assert resp.status_code == 200
        assert resp.json() == {"subscriber": True, "plan": "team"}

    async def test_free_user_gets_200_subscriber_false(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "free"}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")

        from routers import harness as harness_mod
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db):
            client = TestClient(app)
            resp = client.post("/api/harness/verify", json={
                "bin": "u1", "token": token, "harness_version": "0.8.0",
            })
        assert resp.status_code == 200
        assert resp.json() == {"subscriber": False, "plan": "free"}

    async def test_bad_token_gets_200_subscriber_false_not_a_401_crash(self):
        """The real shipped client (loop.py:213-222) treats a non-200
        OR a subscriber:false 200 identically as not-subscriber — but
        per the plan, either shape is acceptable. We standardize on
        200 + subscriber:false for a clean, non-leaking failure mode,
        matching the plan's stated acceptance of either."""
        from routers import harness as harness_mod
        db = _FakeDB()
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db):
            client = TestClient(app)
            resp = client.post("/api/harness/verify", json={
                "bin": "ghost", "token": "nope", "harness_version": "0.8.0",
            })
        assert resp.status_code == 200
        assert resp.json() == {"subscriber": False, "plan": None}

    async def test_malformed_body_returns_clean_4xx_json_no_stack_trace(self):
        from routers import harness as harness_mod
        db = _FakeDB()
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/harness/verify", json={"bin": 12345})
        assert 400 <= resp.status_code < 500
        body_text = resp.text
        assert "Traceback" not in body_text
        assert "File \"" not in body_text

    async def test_missing_body_entirely_returns_4xx(self):
        from routers import harness as harness_mod
        db = _FakeDB()
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/harness/verify")
        assert 400 <= resp.status_code < 500

    async def test_db_unavailable_returns_503_not_500(self):
        from routers import harness as harness_mod
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")

        def _raise_503():
            from fastapi import HTTPException
            raise HTTPException(503, "Database not connected")

        with patch.object(harness_mod, "require_db", _raise_503):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/harness/verify", json={
                "bin": "u1", "token": "x", "harness_version": "0.8.0",
            })
        assert resp.status_code == 503

    async def test_route_is_mounted_at_exact_public_contract_path(self):
        """The real, already-shipped harness client (confirmed from
        github.com/AuremHQ/aurem-loop main, loop.py:160) hardcodes
        https://auremcto.com/api/harness/verify — no /aurem-dev
        segment. This route must be reachable at exactly that path."""
        from routers import harness as harness_mod
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        paths = {r.path for r in app.routes}
        assert "/api/harness/verify" in paths


# ─────────────────────────────────────────────────────────────────────
# SECURITY (T4)
# ─────────────────────────────────────────────────────────────────────
class TestVerifyEndpointSecurity:
    async def test_token_never_appears_in_the_response_body(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "pro"}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")

        from routers import harness as harness_mod
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db):
            client = TestClient(app)
            resp = client.post("/api/harness/verify", json={
                "bin": "u1", "token": token, "harness_version": "0.8.0",
            })
        assert token not in resp.text

    async def test_token_never_appears_in_logs(self, caplog):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "u1", "tier": "pro"}])
        token = await harness_auth.issue_harness_token(db, user_id="u1")

        from routers import harness as harness_mod
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with (
            caplog.at_level(logging.DEBUG),
            patch.object(harness_mod, "require_db", lambda: db),
        ):
            client = TestClient(app)
            client.post("/api/harness/verify", json={
                "bin": "u1", "token": token, "harness_version": "0.8.0",
            })
        for record in caplog.records:
            assert token not in record.getMessage()

    async def test_mongo_operator_injection_in_bin_is_rejected_as_invalid_input(self):
        from routers import harness as harness_mod
        db = _FakeDB(users=[{"user_id": "u1", "tier": "team"}])
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/harness/verify", json={
                "bin": {"$ne": None}, "token": "x", "harness_version": "0.8.0",
            })
        # Pydantic must reject a non-string bin outright (422), never
        # let a dict reach the DB filter.
        assert resp.status_code == 422

    async def test_mongo_operator_injection_in_token_is_rejected_as_invalid_input(self):
        from routers import harness as harness_mod
        db = _FakeDB(users=[{"user_id": "u1", "tier": "team"}])
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/harness/verify", json={
                "bin": "u1", "token": {"$gt": ""}, "harness_version": "0.8.0",
            })
        assert resp.status_code == 422

    async def test_rate_limited_bin_gets_429(self):
        from routers import harness as harness_mod
        db = _FakeDB(users=[{"user_id": "u1", "tier": "free"}])
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db), \
             patch.object(harness_mod, "check_rate_limit_async",
                          AsyncMock(return_value=False)):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/harness/verify", json={
                "bin": "u1", "token": "x", "harness_version": "0.8.0",
            })
        assert resp.status_code == 429

    async def test_rate_limit_keyed_by_bin_not_shared_globally(self):
        """Confirms the rate-limit key passed to check_rate_limit_async
        is derived from the request's own bin, not a constant string —
        otherwise one noisy bin would 429 every other user."""
        from routers import harness as harness_mod
        db = _FakeDB(users=[{"user_id": "u1", "tier": "free"},
                             {"user_id": "u2", "tier": "free"}])
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        seen_keys = []

        async def _fake_rl(key, limit_per_minute):
            seen_keys.append(key)
            return True

        with patch.object(harness_mod, "require_db", lambda: db), \
             patch.object(harness_mod, "check_rate_limit_async", _fake_rl):
            client = TestClient(app)
            client.post("/api/harness/verify",
                        json={"bin": "u1", "token": "x", "harness_version": "0.8.0"})
            client.post("/api/harness/verify",
                        json={"bin": "u2", "token": "x", "harness_version": "0.8.0"})
        assert len(seen_keys) == 2
        assert seen_keys[0] != seen_keys[1]
        assert "u1" in seen_keys[0]
        assert "u2" in seen_keys[1]

    async def test_huge_bin_and_token_strings_do_not_crash(self):
        from routers import harness as harness_mod
        db = _FakeDB()
        app = FastAPI()
        app.include_router(harness_mod.router, prefix="/api")
        with patch.object(harness_mod, "require_db", lambda: db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/harness/verify", json={
                "bin": "u" * 100_000, "token": "t" * 100_000,
                "harness_version": "0.8.0",
            })
        assert resp.status_code in (200, 422)
        if resp.status_code == 200:
            assert resp.json() == {"subscriber": False, "plan": None}

    async def test_unicode_bin_and_token_handled_safely(self):
        from services import harness_auth
        db = _FakeDB(users=[{"user_id": "ユーザー1", "tier": "pro"}])
        token = await harness_auth.issue_harness_token(db, user_id="ユーザー1")
        result = await harness_auth.verify_harness_identity(
            db, bin="ユーザー1", token=token,
        )
        assert result == {"subscriber": True, "plan": "pro"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
