"""
tests/test_aurem_deploy_share_2026_11_21.py

Aurem Deploy Phase 2 — POST /api/deploy/share (routers/deploy_share.py).

Contract under test (see the router module docstring):
  POST /api/deploy/share, Bearer founder/admin JWT
  {project, deploy_id, sha, status, failed_step, predicted_reason,
   suggested_fix, log_tail}
  -> 200 {"thread_id", "title", "delivered": true}
  -> 401 without auth, 400 on a bad payload.

Pattern follows tests/test_regression_iter2026_63_harness_verify_phase_a.py:
a standalone FastAPI app with only this router mounted at /api, a fake
in-memory Mongo collection for `ora_chat_sessions`, and the session
module's get_db patched to return it. Auth is patched to a founder
user for the happy-path/400 tests; the 401 test deliberately uses the
REAL require_admin with no Authorization header, so it proves the
endpoint is actually gated (current_dev raises 401 before any DB
touch when the header is missing).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────────────
# Fake DB — matches the Motor shape services/ora_chat/session.py uses:
# insert_one + update_one with $push / $inc / $set.
# ─────────────────────────────────────────────────────────────────────
class _FakeColl:
    def __init__(self):
        self.docs = []

    def _match(self, d, filt):
        for k, v in (filt or {}).items():
            if d.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

        class _Res:
            inserted_id = "fake-id"

        return _Res()

    async def update_one(self, filt, update):
        for d in self.docs:
            if self._match(d, filt):
                for item in update.get("$push", {}).values():
                    # Only $push target used here is "messages" (a list).
                    d.setdefault("messages", []).append(item)
                for k, v in update.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                d.update(update.get("$set", {}))

                class _Res:
                    matched_count = 1

                return _Res()

        class _Res:
            matched_count = 0

        return _Res()


class _FakeDB:
    def __init__(self):
        self.ora_chat_sessions = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


def _payload(**overrides):
    body = {
        "project": "aurem",
        "deploy_id": "dep-123",
        "sha": "abc1234def5678",
        "status": "failed",
        "failed_step": "health-gate",
        "predicted_reason": "staging slot never answered /api/health",
        "suggested_fix": "check the slot log, then re-deploy",
        "log_tail": "Traceback ...\nConnection refused on 127.0.0.1:8003",
    }
    body.update(overrides)
    return body


@pytest.fixture
def fake_db():
    return _FakeDB()


def _build_app():
    from routers import deploy_share as deploy_share_mod

    app = FastAPI()
    app.include_router(deploy_share_mod.router, prefix="/api")
    return app


@pytest.fixture
def authed_client(fake_db):
    """TestClient with require_admin patched to a founder user and the
    ORA Chat session store pointed at the fake DB."""
    from routers import deploy_share as deploy_share_mod

    app = _build_app()
    with (
        patch.object(
            deploy_share_mod, "require_admin",
            new=AsyncMock(return_value={"user_id": "founder-1"}),
        ),
        patch("services.ora_chat.session.get_db", return_value=fake_db),
        TestClient(app) as client,
    ):
        yield client


def test_happy_path_creates_thread_with_title_and_content(
    authed_client, fake_db,
):
    r = authed_client.post(
        "/api/deploy/share", json=_payload(),
        headers={"Authorization": "Bearer test-founder-jwt"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["delivered"] is True
    assert data["title"] == "Deploy failed: aurem #dep-123"
    assert isinstance(data["thread_id"], str) and len(data["thread_id"]) == 32

    # The thread actually landed in the ORA Chat session store, with
    # the failure context as its first message.
    assert len(fake_db.ora_chat_sessions.docs) == 1
    doc = fake_db.ora_chat_sessions.docs[0]
    assert doc["session_id"] == data["thread_id"]
    assert doc["user_id"] == "founder-1"
    assert doc["title"] == "Deploy failed: aurem #dep-123"
    assert len(doc["messages"]) == 1
    first = doc["messages"][0]
    assert first["role"] == "user"
    for needle in (
        "health-gate",
        "staging slot never answered /api/health",
        "check the slot log, then re-deploy",
        "Connection refused on 127.0.0.1:8003",
        "abc1234def5678",
    ):
        assert needle in first["content"], needle


def test_optional_fields_default_to_empty(authed_client, fake_db):
    r = authed_client.post(
        "/api/deploy/share",
        json={
            "project": "aurem",
            "deploy_id": "dep-124",
            "sha": "deadbeef",
            "status": "failed",
        },
        headers={"Authorization": "Bearer test-founder-jwt"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Deploy failed: aurem #dep-124"
    first = fake_db.ora_chat_sessions.docs[0]["messages"][0]
    assert "unknown" in first["content"]


def test_unauthenticated_returns_401(fake_db):
    # REAL require_admin, no Authorization header: current_dev raises
    # 401 before any DB touch. This proves the endpoint is gated.
    app = _build_app()
    with (
        patch("services.ora_chat.session.get_db", return_value=fake_db),
        TestClient(app) as client,
    ):
        r = client.post("/api/deploy/share", json=_payload())
    assert r.status_code == 401, r.text
    assert fake_db.ora_chat_sessions.docs == []


def test_bad_payload_returns_400(authed_client):
    # Empty object — every required field missing.
    r = authed_client.post(
        "/api/deploy/share", json={},
        headers={"Authorization": "Bearer test-founder-jwt"},
    )
    assert r.status_code == 400, r.text
    assert "project" in r.text

    # Missing one required field.
    r = authed_client.post(
        "/api/deploy/share", json=_payload(deploy_id=None),
        headers={"Authorization": "Bearer test-founder-jwt"},
    )
    assert r.status_code == 400, r.text

    # Wrong shape entirely.
    r = authed_client.post(
        "/api/deploy/share", json=[1, 2, 3],
        headers={"Authorization": "Bearer test-founder-jwt"},
    )
    assert r.status_code == 400, r.text

    # Malformed JSON.
    r = authed_client.post(
        "/api/deploy/share", content="{not json",
        headers={
            "Authorization": "Bearer test-founder-jwt",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400, r.text
