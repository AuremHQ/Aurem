"""tests/test_regression_iter2026_73_payment_webhook_ordering_and_loop_abort.py

Security audit findings (P1):

1. Payment webhook ordering (routers/payments.py, stripe_webhook) — the
   `checkout.session.expired` branch updated `cto_payments.status`
   unconditionally. Stripe webhook delivery is at-least-once and NOT
   guaranteed in-order; an out-of-order/retried "expired" event
   arriving after the session's own "completed" event could overwrite
   an already-paid ledger row's status back to "expired". Fixed by
   adding a `{"status": {"$ne": "complete"}}` guard to the query — an
   explicit state rule: complete never downgrades to expired.

2. Loop abort fallback (routers/loop.py, POST /loop/{id}/cancel,
   "no live engine" branch) — unconditionally wrote `state="aborted"`
   with no precondition on the loop's CURRENT state. A loop that had
   ACTUALLY already reached a terminal state (completed/failed/
   expired) in the brief window before this fallback ran could have
   that real result silently overwritten with "aborted". Fixed with a
   conditional update gated on the current state still being
   non-terminal; an already-terminal loop is now left untouched and
   its real state is reported back instead of a false "aborted".

Both follow the exact test conventions already used elsewhere in this
suite: payments via `stripe.Webhook.construct_event` monkeypatch (see
test_regression_iter2026_11a), loop cancel via a real HTTP call against
the live backend with a synthetic loop_sessions doc that has no live
in-process engine (see test_iter212m145's fallback-path coverage).
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)

BACKEND = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BACKEND}/api/aurem-dev"


def _uid(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def db():
    if not _live_available:
        pytest.skip("MONGO_URL/DB_NAME not set")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    database = client[DB_NAME]
    yield database
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.cto_payments.delete_many({"session_id": {"$regex": "^cs_test_wallet_test_"}})
        await db.loop_sessions.delete_many({"loop_id": {"$regex": "^loop_wallet_test_"}})
        await db.loop_events.delete_many({"loop_id": {"$regex": "^loop_wallet_test_"}})


def _wire_webhook(payments, db, monkeypatch, event: dict):
    monkeypatch.setattr(
        payments.stripe.Webhook, "construct_event",
        staticmethod(lambda *a, **kw: event),
    )
    monkeypatch.setattr(payments, "require_db", lambda: db)
    monkeypatch.setattr(payments, "_require_stripe", lambda: None)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")


class _Req:
    headers = {"stripe-signature": "sig"}

    async def body(self):
        return b"{}"


def _expired_event(session_id: str) -> dict:
    return {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "type": "checkout.session.expired",
        "data": {"object": {"id": session_id}},
    }


class TestPaymentWebhookCannotDowngradeACompletedSession:
    async def test_out_of_order_expired_event_does_not_touch_a_complete_row(self, db, monkeypatch):
        from routers import payments
        session_id = f"cs_test_{_uid('complete')}"
        await db.cto_payments.insert_one({
            "session_id": session_id, "status": "complete",
            "payment_status": "paid", "amount": 9.0,
            "paid_at": time.time(), "updated_at": time.time(),
        })
        _wire_webhook(payments, db, monkeypatch, _expired_event(session_id))
        result = await payments.stripe_webhook(_Req())
        assert result == {"received": True} or isinstance(result, dict)

        row = await db.cto_payments.find_one({"session_id": session_id})
        assert row["status"] == "complete", "a completed payment must never be downgraded to expired"
        assert row["payment_status"] == "paid"

    async def test_normal_pending_session_still_expires_as_before(self, db, monkeypatch):
        """Regression: the actual common case (Iter 352's original
        fix — an abandoned, never-paid checkout) must still work."""
        from routers import payments
        session_id = f"cs_test_{_uid('pending')}"
        await db.cto_payments.insert_one({
            "session_id": session_id, "status": "pending",
            "payment_status": "pending", "updated_at": time.time(),
        })
        _wire_webhook(payments, db, monkeypatch, _expired_event(session_id))
        await payments.stripe_webhook(_Req())

        row = await db.cto_payments.find_one({"session_id": session_id})
        assert row["status"] == "expired"
        assert row["payment_status"] == "expired"


class TestLoopAbortCannotOverwriteATerminalLoop:
    async def _signup(self):
        email = f"loopabort-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}@aurem.dev"
        pw = "LoopAbortTest2026!"
        r = requests.post(f"{API}/auth/signup",
                           json={"email": email, "password": pw, "name": "Loop Abort Test"}, timeout=15)
        assert r.status_code == 200, r.text
        return r.json()["token"], r.json()["user_id"]

    async def test_cancel_on_an_already_completed_loop_leaves_it_completed(self, db):
        token, user_id = await self._signup()
        loop_id = f"loop_{_uid('done')}"
        await db.loop_sessions.insert_one({
            "loop_id": loop_id, "user_id": user_id,
            "state": "completed", "phase": "ship",
            "project_id": None, "updated_at": time.time(),
        })
        r = requests.post(f"{API}/loop/{loop_id}/cancel",
                           headers={"Authorization": f"Bearer {token}"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("state") == "completed", \
            f"a completed loop's real state must be reported, not a false 'aborted': {body}"
        assert body.get("already_terminal") is True

        row = await db.loop_sessions.find_one({"loop_id": loop_id})
        assert row["state"] == "completed", "the completed loop's own record must be untouched"

    async def test_cancel_on_a_running_loop_still_aborts_it(self, db):
        """Regression: the actual intended case (a genuinely in-
        progress loop with no live in-process engine, e.g. a
        different worker crashed) must still abort normally."""
        token, user_id = await self._signup()
        loop_id = f"loop_{_uid('running')}"
        await db.loop_sessions.insert_one({
            "loop_id": loop_id, "user_id": user_id,
            "state": "executing", "phase": "execute",
            "project_id": None, "updated_at": time.time(),
        })
        r = requests.post(f"{API}/loop/{loop_id}/cancel",
                           headers={"Authorization": f"Bearer {token}"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("state") == "aborted"
        assert not body.get("already_terminal")

        row = await db.loop_sessions.find_one({"loop_id": loop_id})
        assert row["state"] == "aborted"

    async def test_cancel_on_a_failed_loop_is_also_left_alone(self, db):
        """'expired' is intentionally rehydratable (a different, pre-
        existing code path formally cancels it) — but 'failed' is not,
        and must hit the same fallback guard as 'completed'."""
        token, user_id = await self._signup()
        loop_id = f"loop_{_uid('failed')}"
        await db.loop_sessions.insert_one({
            "loop_id": loop_id, "user_id": user_id,
            "state": "failed", "phase": "execute",
            "project_id": None, "updated_at": time.time(),
        })
        r = requests.post(f"{API}/loop/{loop_id}/cancel",
                           headers={"Authorization": f"Bearer {token}"}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("state") == "failed"
        row = await db.loop_sessions.find_one({"loop_id": loop_id})
        assert row["state"] == "failed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
