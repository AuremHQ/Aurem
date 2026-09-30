"""
test_regression_iter403_github_funnel_meta_cap_and_server_user_id.py
=========================================================================

Medium finding (code-read security audit, 2026-09-30):
POST /funnel/github/event (routers/github_funnel.py) is intentionally
anonymous (pre-signup CTA clicks have no session), which meant:

  1. `meta: Optional[dict]` had no size limit at all — any caller could
     bloat `github_funnel_events` (storage cost) or poison the
     `/funnel/github/stats` aggregation (`meta.reason`/`meta.outcome`
     feed directly into the admin funnel report).
  2. `user_id` was taken verbatim from the client body — an
     unauthenticated caller could claim to BE any real user_id,
     attributing funnel/conversion events to an arbitrary victim.

Fix: `meta` is now capped (<=20 keys, <=2048 bytes serialized).
`user_id` is now always server-derived from a valid Bearer token if
present (ignoring whatever the client body says either way); with no
valid token, it's always None — the endpoint stays fully open to
logged-out callers, it just can no longer be tricked into attributing
an event to someone else.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest

API = "http://localhost:8001/api/aurem-dev"


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


async def _cleanup(session_id: str) -> None:
    db = _db()
    await db.github_funnel_events.delete_many({"session_id": session_id})


async def _fetch_row(session_id: str) -> dict:
    db = _db()
    return await db.github_funnel_events.find_one({"session_id": session_id})


def _post_event(session_id: str, **kw) -> httpx.Response:
    return httpx.post(
        f"{API}/funnel/github/event",
        json={"stage": "cta_click", "source": "login", "session_id": session_id, **kw},
        timeout=10,
    )


class TestMetaSizeCap:
    def test_reasonable_meta_still_accepted(self):
        sid = f"c_test_{uuid.uuid4().hex[:16]}"
        try:
            r = _post_event(sid, meta={"reason": "popup_closed", "outcome": "abandoned"})
            assert r.status_code == 200, r.text
        finally:
            import asyncio
            asyncio.run(_cleanup(sid))

    def test_oversized_meta_rejected(self):
        sid = f"c_test_{uuid.uuid4().hex[:16]}"
        huge_meta = {"payload": "x" * 5000}
        r = _post_event(sid, meta=huge_meta)
        assert r.status_code == 422, r.text

    def test_too_many_meta_keys_rejected(self):
        sid = f"c_test_{uuid.uuid4().hex[:16]}"
        many_keys_meta = {f"k{i}": "v" for i in range(50)}
        r = _post_event(sid, meta=many_keys_meta)
        assert r.status_code == 422, r.text


class TestUserIdIsServerDerivedNotClientSupplied:
    def test_client_supplied_user_id_is_ignored_when_anonymous(self):
        """The actual attack: an unauthenticated caller claims to be
        some other real user_id — must be silently ignored, not stored."""
        sid = f"c_test_{uuid.uuid4().hex[:16]}"
        try:
            victim_user_id = "some-real-victim-user-id-999"
            r = _post_event(sid, user_id=victim_user_id)
            assert r.status_code == 200, r.text
            import asyncio
            row = asyncio.run(_fetch_row(sid))
            assert row is not None
            assert row["user_id"] != victim_user_id
            assert row["user_id"] is None
        finally:
            import asyncio
            asyncio.run(_cleanup(sid))

    def test_authenticated_caller_gets_their_own_real_user_id_not_client_body(self):
        """Regression: a genuinely logged-in caller's OWN events should
        still be correctly attributed — server derives it from their
        token, ignoring whatever (if anything) the body claims."""
        email = f"pytest-funnel403-{uuid.uuid4().hex[:8]}@example.com"
        password = "Funnel403Pass!2026"
        signup = httpx.post(f"{API}/auth/signup", json={
            "email": email, "password": password, "name": "Funnel403",
        }, timeout=20)
        assert signup.status_code in (200, 201), signup.text
        token = signup.json()["token"]
        real_user_id = signup.json()["user_id"]

        sid = f"c_test_{uuid.uuid4().hex[:16]}"
        try:
            r = httpx.post(
                f"{API}/funnel/github/event",
                json={
                    "stage": "cta_click", "source": "login", "session_id": sid,
                    "user_id": "someone-elses-claimed-id",
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            assert r.status_code == 200, r.text
            import asyncio
            row = asyncio.run(_fetch_row(sid))
            assert row is not None
            assert row["user_id"] == real_user_id
            assert row["user_id"] != "someone-elses-claimed-id"
        finally:
            import asyncio
            asyncio.run(_cleanup(sid))

    def test_no_auth_header_at_all_still_works_anonymously(self):
        """The whole point of this endpoint — must never require auth."""
        sid = f"c_test_{uuid.uuid4().hex[:16]}"
        try:
            r = _post_event(sid)
            assert r.status_code == 200, r.text
        finally:
            import asyncio
            asyncio.run(_cleanup(sid))
