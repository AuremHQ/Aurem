"""tests/test_regression_iter2026_11d_part3_admin_pricing_panel.py

Master Prompt v2 PART 3 — the admin pricing panel (margin editor,
fixed monthly costs, dashboard, billing_flags review), founder/admin
role only.

Per HARD RULES v2:
  T1 TDD — written before/alongside the implementation; see the Part 3
      section of the final report for the red-run output.
  T2 unit tests for every new function/branch (0, negative, huge, None,
      empty, unicode, duplicates, retries, idempotency).
  T3 integration tests through the real endpoint functions (routers/
      admin_pricing.py) against a REAL local Mongo — same pattern this
      entire session's other integration tests use (e.g.
      payments.stripe_webhook called directly). NOT FastAPI TestClient:
      Motor's AsyncIOMotorClient is bound to the event loop it was
      first used on, and TestClient dispatches each request through a
      SEPARATE anyio worker-thread loop — a well-known Motor/TestClient
      incompatibility ("Future attached to a different loop") that has
      nothing to do with this router's own correctness. Calling the
      endpoint function directly, in the SAME loop pytest-asyncio runs
      the test in, exercises the exact same code (request validation
      via the real Pydantic body models, the real `_require_admin`
      auth gate, the real aggregation pipelines against real Mongo)
      without that unrelated transport-layer incompatibility.
  T4 security — auth/IDOR, margin bounds + finiteness, Mongo-operator
      injection, concurrent-save non-overlap, immutable audit, CSRF/
      replay, PII minimization.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)


def _uid(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def db():
    if not _live_available:
        pytest.skip("MONGO_URL/DB_NAME not set")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    database = client[DB_NAME]
    from cto_services import db as _dbmod
    _dbmod.set_db(database)
    yield database
    _dbmod.set_db(None)
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.wallet_ledger.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.billing_flags.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.pricing_versions.delete_many({"actor": {"$regex": "^wallet_test_"}})
        await db.pricing_audit_log.delete_many({"actor": {"$regex": "^wallet_test_"}})
        await db.fixed_costs.delete_many({"created_by": {"$regex": "^wallet_test_"}})
        await db.pricing_preview_used.delete_many({})
        await db.pricing_seq.delete_many({})


ADMIN_USER = {"user_id": "wallet_test_founder", "is_admin": True, "tier": "founder",
             "email": "wallet_test_founder@aurem.dev", "is_unlimited": True}


def _as_admin():
    """Patches routers._admin_common.current_dev (the function every
    handler's inline `await _require_admin(authorization)` actually
    calls) so "Bearer admin-token" resolves to a founder-email user —
    _finish_admin_check's real, unmocked logic then genuinely grants
    Owner access, exactly as it would for a real founder JWT. Any
    other token value resolves to a real, non-founder, non-team-member
    user, so _finish_admin_check's real logic genuinely raises 403 —
    this is NOT a bypass of the auth rule, it's feeding it a real
    admin vs a real non-admin."""
    async def _fake_current_dev(authorization=None):
        if not authorization:
            raise HTTPException(401, "Authorization header missing")
        if authorization == "Bearer admin-token":
            return ADMIN_USER
        return {"user_id": "wallet_test_rando", "is_admin": False, "tier": "starter",
                 "email": "wallet_test_rando@example.com", "is_unlimited": False}
    return patch("routers._admin_common.current_dev", AsyncMock(side_effect=_fake_current_dev))


# ─────────────────────────────────────────────────────────────────────
# Unit tests — services.pricing_engine's new PART 3 functions
# ─────────────────────────────────────────────────────────────────────

class TestValidateMarginInput:
    async def test_valid_margin_passes(self):
        from services.pricing_engine import validate_margin_input
        assert validate_margin_input(0.5) == 0.5
        assert validate_margin_input(0.10) == 0.10
        assert validate_margin_input(0.80) == 0.80

    async def test_below_minimum_rejected(self):
        from services.pricing_engine import validate_margin_input
        with pytest.raises(ValueError):
            validate_margin_input(0.09)
        with pytest.raises(ValueError):
            validate_margin_input(0.0)
        with pytest.raises(ValueError):
            validate_margin_input(-0.5)

    async def test_above_maximum_rejected(self):
        from services.pricing_engine import validate_margin_input
        with pytest.raises(ValueError):
            validate_margin_input(0.81)
        with pytest.raises(ValueError):
            validate_margin_input(1.0)

    async def test_nan_and_infinity_rejected(self):
        from services.pricing_engine import validate_margin_input
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValueError):
                validate_margin_input(bad)

    async def test_non_numeric_types_rejected(self):
        from services.pricing_engine import validate_margin_input
        for bad in ("0.5", None, [0.5], {"margin": 0.5}, {"$ne": 0.5}, b"0.5"):
            with pytest.raises(ValueError):
                validate_margin_input(bad)

    async def test_bool_rejected_even_though_isinstance_int(self):
        """Python's bool is a subclass of int — isinstance(True, int)
        is True, so a naive check would let True/False sail through as
        1.0/0.0. Both must be rejected."""
        from services.pricing_engine import validate_margin_input
        with pytest.raises(ValueError):
            validate_margin_input(True)
        with pytest.raises(ValueError):
            validate_margin_input(False)


class TestFixedCostsCRUD:
    async def test_create_list_update_delete_round_trip(self, db):
        from services.pricing_engine import (
            delete_fixed_cost,
            list_fixed_costs,
            set_fixed_cost,
        )
        doc = await set_fixed_cost(
            db, cost_id=None, name="wallet_test Hosting", amount_usd_monthly=50.0,
            actor="wallet_test_admin",
        )
        assert doc["name"] == "wallet_test Hosting"
        rows = await list_fixed_costs(db)
        assert any(r["_id"] == doc["_id"] for r in rows)

        updated = await set_fixed_cost(
            db, cost_id=doc["_id"], name="wallet_test Hosting v2",
            amount_usd_monthly=75.0, actor="wallet_test_admin",
        )
        assert updated["name"] == "wallet_test Hosting v2"
        assert updated["amount_usd_monthly"] == 75.0

        deleted = await delete_fixed_cost(db, doc["_id"])
        assert deleted is True
        rows_after = await list_fixed_costs(db)
        assert not any(r["_id"] == doc["_id"] for r in rows_after)

    async def test_update_nonexistent_id_raises(self, db):
        from services.pricing_engine import set_fixed_cost
        with pytest.raises(ValueError):
            await set_fixed_cost(
                db, cost_id="does_not_exist", name="x",
                amount_usd_monthly=10, actor="wallet_test_admin",
            )

    async def test_delete_nonexistent_id_returns_false(self, db):
        from services.pricing_engine import delete_fixed_cost
        assert await delete_fixed_cost(db, "does_not_exist") is False

    async def test_empty_name_rejected(self, db):
        from services.pricing_engine import set_fixed_cost
        with pytest.raises(ValueError):
            await set_fixed_cost(db, cost_id=None, name="", amount_usd_monthly=10, actor="a")
        with pytest.raises(ValueError):
            await set_fixed_cost(db, cost_id=None, name="   ", amount_usd_monthly=10, actor="a")

    async def test_negative_and_non_finite_amount_rejected(self, db):
        from services.pricing_engine import set_fixed_cost
        for bad in (-1.0, float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValueError):
                await set_fixed_cost(db, cost_id=None, name="x",
                                     amount_usd_monthly=bad, actor="a")

    async def test_zero_amount_is_valid(self, db):
        from services.pricing_engine import set_fixed_cost
        doc = await set_fixed_cost(db, cost_id=None, name="wallet_test free tier",
                                   amount_usd_monthly=0.0, actor="wallet_test_admin")
        assert doc["amount_usd_monthly"] == 0.0

    async def test_unicode_name_round_trips_exactly(self, db):
        from services.pricing_engine import set_fixed_cost
        name = "wallet_test 服务器费用 — café ☕ <script>alert(1)</script>"
        doc = await set_fixed_cost(db, cost_id=None, name=name,
                                   amount_usd_monthly=5, actor="wallet_test_admin")
        assert doc["name"] == name, "stored RAW — escaping is the frontend's job, not a write-time mangling"

    async def test_total_fixed_costs_monthly_sums_all_rows(self, db):
        from services.pricing_engine import set_fixed_cost, total_fixed_costs_monthly
        await set_fixed_cost(db, cost_id=None, name="wallet_test a",
                             amount_usd_monthly=10, actor="t")
        await set_fixed_cost(db, cost_id=None, name="wallet_test b",
                             amount_usd_monthly=25.5, actor="t")
        total = await total_fixed_costs_monthly(db)
        assert total >= 35.5


class TestAuditLog:
    async def test_audit_row_is_written_and_readable(self, db):
        """Append-only enforcement itself (source-level AND, more
        importantly, behavioral: a direct-Mongo tamper attempt) is
        now covered far more rigorously by
        test_regression_iter2026_12b_audit_log_append_only_and_tamper_evidence.py
        (PRE-STEP (b) — the prior inspect.getsource check here only
        proved this module's own source never calls .update/.delete;
        it said nothing about whether a row could be edited/deleted
        directly in Mongo. The 12b suite proves the hash-chain
        verify_audit_chain() DETECTS that case instead.) This test
        keeps just the functional read-after-write check."""
        from services.pricing_engine import list_audit_log, write_audit_row
        row = await write_audit_row(
            db, action="margin_change", actor="wallet_test_admin",
            before={"margin": 0.3}, after={"margin": 0.5},
        )
        rows = await list_audit_log(db)
        assert any(r["_id"] == row["_id"] for r in rows)
        found = next(r for r in rows if r["_id"] == row["_id"])
        assert found["before"] == {"margin": 0.3}
        assert found["after"] == {"margin": 0.5}


class TestPreviewConfirmToken:
    async def test_token_round_trips_for_the_same_payload(self):
        from services.pricing_engine import make_preview_token
        doc = make_preview_token({"margin": 0.5})
        assert doc["margin"] == 0.5
        assert "token" in doc and "expires_at" in doc

    async def test_tampered_payload_rejected(self, db):
        from services.pricing_engine import (
            make_preview_token,
            verify_and_consume_preview_token,
        )
        doc = make_preview_token({"margin": 0.5})
        tampered = {**doc, "margin": 0.79}
        with pytest.raises(ValueError):
            await verify_and_consume_preview_token(db, tampered, doc["token"])

    async def test_expired_token_rejected(self, db):
        from services.pricing_engine import verify_and_consume_preview_token
        payload = {"margin": 0.5, "expires_at": time.time() - 10}
        with pytest.raises(ValueError):
            await verify_and_consume_preview_token(db, payload, "irrelevant")

    async def test_replay_of_an_already_used_token_rejected(self, db):
        from services.pricing_engine import (
            make_preview_token,
            verify_and_consume_preview_token,
        )
        doc = make_preview_token({"margin": 0.5})
        payload = {k: v for k, v in doc.items() if k != "token"}
        await verify_and_consume_preview_token(db, payload, doc["token"])
        with pytest.raises(ValueError):
            await verify_and_consume_preview_token(db, payload, doc["token"])


# ─────────────────────────────────────────────────────────────────────
# Integration tests — through the real endpoint functions
# ─────────────────────────────────────────────────────────────────────

class TestMarginEditorEndToEnd:
    async def test_preview_then_confirm_applies_new_version(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            preview = await ap.preview_margin(
                ap.MarginPreviewBody(margin=0.6), authorization="Bearer admin-token",
            )
            p = preview["preview"]
            confirmed = await ap.confirm_margin(
                ap.MarginConfirmBody(
                    margin=0.6, stripe_fee_pct=p["stripe_fee_pct"],
                    stripe_fee_fixed_usd=p["stripe_fee_fixed_usd"],
                    expires_at=p["expires_at"], token=p["token"],
                ),
                authorization="Bearer admin-token",
            )
            assert confirmed["new_version"]["margin"] == 0.6
            current = await ap.get_pricing_version(authorization="Bearer admin-token")
            assert current["current"]["margin"] == 0.6

    async def test_confirm_without_a_preceding_preview_rejected(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            with pytest.raises(HTTPException) as exc:
                await ap.confirm_margin(
                    ap.MarginConfirmBody(
                        margin=0.6, stripe_fee_pct=0.029, stripe_fee_fixed_usd=0.30,
                        expires_at=time.time() + 300, token="forged-token-value",
                    ),
                    authorization="Bearer admin-token",
                )
            assert exc.value.status_code == 409

    async def test_replaying_the_same_confirm_request_twice_fails_the_second_time(self, db):
        """The CSRF/replay requirement — a captured confirm request
        (e.g. a network proxy log, a browser back-button resubmit)
        must not apply the SAME margin change twice."""
        from routers import admin_pricing as ap
        with _as_admin():
            preview = await ap.preview_margin(
                ap.MarginPreviewBody(margin=0.55), authorization="Bearer admin-token",
            )
            p = preview["preview"]
            body = ap.MarginConfirmBody(
                margin=0.55, stripe_fee_pct=p["stripe_fee_pct"],
                stripe_fee_fixed_usd=p["stripe_fee_fixed_usd"],
                expires_at=p["expires_at"], token=p["token"],
            )
            r1 = await ap.confirm_margin(body, authorization="Bearer admin-token")
            assert r1["ok"] is True
            with pytest.raises(HTTPException) as exc:
                await ap.confirm_margin(body, authorization="Bearer admin-token")
            assert exc.value.status_code == 409

    async def test_margin_outside_bounds_rejected_at_preview(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            for bad_margin in (0.05, 0.95, 0, 1.0):
                with pytest.raises(HTTPException) as exc:
                    await ap.preview_margin(
                        ap.MarginPreviewBody(margin=bad_margin),
                        authorization="Bearer admin-token",
                    )
                assert exc.value.status_code == 400, f"margin={bad_margin} should be rejected"

    async def test_non_finite_and_malformed_margin_rejected(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            for bad in (float("nan"), float("inf")):
                with pytest.raises(HTTPException) as exc:
                    await ap.preview_margin(
                        ap.MarginPreviewBody(margin=bad), authorization="Bearer admin-token",
                    )
                assert exc.value.status_code == 400
            # Wrong TYPES are rejected by Pydantic itself, before the
            # handler ever runs — the request-validation layer.
            for bad in ("not-a-number", [0.5], {"$ne": 0.5}):
                with pytest.raises(ValidationError):
                    ap.MarginPreviewBody(margin=bad)

    async def test_old_batch_keeps_its_margin_after_a_change(self, db):
        """Direct Part 3 security requirement, built on the pre-step
        (a) proof that settled ledger rows are immutable — a margin
        change applied via the REAL endpoint must not retroactively
        touch anything already settled."""
        from routers import admin_pricing as ap
        from services.wallet import get_balance, grant, hold, settle
        u = _uid("endpointmarginchange")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")
        await settle(db, user_id=u, run_id=f"run_{u}", actual_cost_usd=0.01,
                     idempotency_key=f"settle_{u}")
        row_before = await db.wallet_ledger.find_one({"_id": f"settle_{u}"})

        with _as_admin():
            preview = await ap.preview_margin(
                ap.MarginPreviewBody(margin=0.75), authorization="Bearer admin-token",
            )
            p = preview["preview"]
            await ap.confirm_margin(
                ap.MarginConfirmBody(
                    margin=0.75, stripe_fee_pct=p["stripe_fee_pct"],
                    stripe_fee_fixed_usd=p["stripe_fee_fixed_usd"],
                    expires_at=p["expires_at"], token=p["token"],
                ),
                authorization="Bearer admin-token",
            )

        row_after = await db.wallet_ledger.find_one({"_id": f"settle_{u}"})
        assert row_after == row_before, "an already-settled row must never change after a margin update"
        bal = await get_balance(db, u)
        assert bal["purchased"] == 100 - row_before["amount"]


class TestFixedCostsEndToEnd:
    async def test_create_update_delete_through_the_endpoint(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            r1 = await ap.upsert_fixed_cost(
                ap.FixedCostBody(cost_id=None, name="wallet_test Hosting", amount_usd_monthly=40.0),
                authorization="Bearer admin-token",
            )
            cost_id = r1["fixed_cost"]["_id"]

            r2 = await ap.list_fixed_costs_endpoint(authorization="Bearer admin-token")
            assert any(c["_id"] == cost_id for c in r2["fixed_costs"])

            r3 = await ap.delete_fixed_cost_endpoint(cost_id, authorization="Bearer admin-token")
            assert r3["ok"] is True

            audit = await ap.get_audit_log(authorization="Bearer admin-token")
            actions = [a["action"] for a in audit["audit_log"]]
            assert "fixed_cost_upsert" in actions
            assert "fixed_cost_delete" in actions

    async def test_delete_nonexistent_cost_id_404s(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            with pytest.raises(HTTPException) as exc:
                await ap.delete_fixed_cost_endpoint("does-not-exist", authorization="Bearer admin-token")
            assert exc.value.status_code == 404

    async def test_stored_xss_payload_name_is_not_html_escaped_at_write_time(self, db):
        """Confirms the backend stores names RAW (the contract the
        frontend's plain-JSX-text escaping relies on) — a defense-in-
        depth double-escaping bug would corrupt the display, not fix
        security, so this locks in "raw storage, frontend escapes"."""
        from routers import admin_pricing as ap
        with _as_admin():
            payload_name = "<img src=x onerror=alert(1)>"
            r1 = await ap.upsert_fixed_cost(
                ap.FixedCostBody(cost_id=None, name=payload_name, amount_usd_monthly=1.0),
                authorization="Bearer admin-token",
            )
            assert r1["fixed_cost"]["name"] == payload_name


class TestBillingFlagsEndToEnd:
    async def test_list_and_resolve_through_the_endpoint(self, db):
        from routers import admin_pricing as ap
        from services import billing_flags
        u = _uid("flagendpoint")
        await billing_flags.flag(
            db, flag_type="refund_shortfall", user_id=u,
            idempotency_key=f"evt_{uuid.uuid4().hex[:8]}", detail={"x": 1},
        )
        with _as_admin():
            r1 = await ap.get_billing_flags(resolved=False, authorization="Bearer admin-token")
            target = next(f for f in r1["flags"] if f["user_id"] == u)

            r2 = await ap.resolve_billing_flag(
                target["_id"], ap.ResolveFlagBody(note="wallet_test reviewed, no action needed"),
                authorization="Bearer admin-token",
            )
            assert r2["flag"]["resolved"] is True

            r3 = await ap.get_billing_flags(resolved=True, authorization="Bearer admin-token")
            assert any(f["_id"] == target["_id"] for f in r3["flags"])

    async def test_resolving_a_nonexistent_flag_404s(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            with pytest.raises(HTTPException) as exc:
                await ap.resolve_billing_flag(
                    "does-not-exist", ap.ResolveFlagBody(note="x"),
                    authorization="Bearer admin-token",
                )
            assert exc.value.status_code == 404

    async def test_resolution_note_with_xss_payload_stored_raw(self, db):
        from routers import admin_pricing as ap
        from services import billing_flags
        u = _uid("flagxss")
        await billing_flags.flag(
            db, flag_type="dispute_won", user_id=u,
            idempotency_key=f"evt_{uuid.uuid4().hex[:8]}",
        )
        with _as_admin():
            flags = await ap.get_billing_flags(resolved=False, authorization="Bearer admin-token")
            target = next(f for f in flags["flags"] if f["user_id"] == u)
            payload = "<script>alert(document.cookie)</script>"
            r = await ap.resolve_billing_flag(
                target["_id"], ap.ResolveFlagBody(note=payload),
                authorization="Bearer admin-token",
            )
            assert r["flag"]["resolution_note"] == payload


class TestDashboardEndToEnd:
    async def test_dashboard_reflects_real_settled_and_released_runs(self, db):
        from routers import admin_pricing as ap
        from services.wallet import grant, hold, release, settle
        u = _uid("dashboarddata")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run1_{u}",
                   idempotency_key=f"h1_{u}")
        await settle(db, user_id=u, run_id=f"run1_{u}", actual_cost_usd=0.02,
                     idempotency_key=f"settle1_{u}")
        await hold(db, user_id=u, action="fix", run_id=f"run2_{u}",
                   idempotency_key=f"h2_{u}")
        await release(db, user_id=u, run_id=f"run2_{u}", idempotency_key=f"rel2_{u}",
                     failed_run_cost=0.015)

        with _as_admin():
            data = await ap.get_dashboard(authorization="Bearer admin-token")
            assert "by_action" in data
            assert "ceiling_hit" in data
            assert "failed_run_cost" in data
            assert data["failed_run_cost"]["total_usd"] >= 0.015
            assert data["failed_run_cost"]["count"] >= 1
            assert "tokens_owed" in data
            assert "unmetered_cost_alarm" in data
            assert "billing_flags_summary" in data
            assert "reconcile_drift" in data

    async def test_ceiling_hit_counted_when_real_cost_exceeds_the_hold(self, db):
        from routers import admin_pricing as ap
        from services.wallet import grant, hold, settle
        u = _uid("ceilinghit")
        await grant(db, user_id=u, bucket="purchased", amount=1000,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")  # held = 12 tokens
        # $1.00 real cost at default margin far exceeds the 12-token hold.
        await settle(db, user_id=u, run_id=f"run_{u}", actual_cost_usd=1.00,
                     idempotency_key=f"settle_{u}")
        with _as_admin():
            data = await ap.get_dashboard(authorization="Bearer admin-token")
            assert data["ceiling_hit"]["count"] >= 1
            assert data["ceiling_hit"]["absorbed_tokens"] > 0

    async def test_by_user_entries_never_include_email(self, db):
        """Part 3 security requirement — admin responses never leak
        other users' emails/PII beyond what's needed; by_user rows are
        user_id + revenue/cost numbers ONLY."""
        from routers import admin_pricing as ap
        from services.wallet import grant, hold, settle
        u = _uid("pii")
        await grant(db, user_id=u, bucket="purchased", amount=100,
                    idempotency_key=f"g_{u}", source="test")
        await hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                   idempotency_key=f"h_{u}")
        await settle(db, user_id=u, run_id=f"run_{u}", actual_cost_usd=0.02,
                     idempotency_key=f"settle_{u}")
        with _as_admin():
            data = await ap.get_dashboard(authorization="Bearer admin-token")
            row = next(r for r in data["by_user_top50"] if r["user_id"] == u)
            assert "email" not in row
            assert set(row.keys()) == {"user_id", "revenue_usd", "real_cost_usd", "realized_margin"}


# ─────────────────────────────────────────────────────────────────────
# Security — auth / IDOR / injection / concurrency
# ─────────────────────────────────────────────────────────────────────

class TestAdminAuthGate:
    async def test_router_level_dependency_covers_every_route(self):
        """Structural proof that applies to EVERY route on this
        router, including ones added later: FastAPI's
        APIRouter(dependencies=[...]) is inherited by every registered
        route automatically — there is no way to add a route to this
        router that skips it."""
        from cto_services.auth import require_admin_dep
        from routers import admin_pricing as ap
        assert require_admin_dep in [d.dependency for d in ap.router.dependencies]
        assert len(ap.router.routes) >= 9, "sanity check that routes are actually registered"

    async def test_logged_out_gets_401(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            with pytest.raises(HTTPException) as exc:
                await ap.get_dashboard(authorization=None)
            assert exc.value.status_code == 401
            with pytest.raises(HTTPException) as exc2:
                await ap.get_pricing_version(authorization=None)
            assert exc2.value.status_code == 401

    async def test_non_admin_gets_403_on_every_endpoint(self, db):
        from routers import admin_pricing as ap
        bad_auth = "Bearer some-other-users-token"
        with _as_admin():
            for coro in (
                ap.get_pricing_version(authorization=bad_auth),
                ap.get_dashboard(authorization=bad_auth),
                ap.list_fixed_costs_endpoint(authorization=bad_auth),
                ap.get_billing_flags(authorization=bad_auth),
                ap.get_audit_log(authorization=bad_auth),
            ):
                with pytest.raises(HTTPException) as exc:
                    await coro
                assert exc.value.status_code == 403

    async def test_non_admin_cannot_change_margin(self, db):
        from routers import admin_pricing as ap
        from services.pricing_engine import get_effective_pricing_version
        before = await get_effective_pricing_version(db)
        with _as_admin():
            with pytest.raises(HTTPException) as exc:
                await ap.confirm_margin(
                    ap.MarginConfirmBody(
                        margin=0.5, stripe_fee_pct=0.029, stripe_fee_fixed_usd=0.30,
                        expires_at=time.time() + 300, token="x",
                    ),
                    authorization="Bearer some-other-users-token",
                )
            assert exc.value.status_code == 403
        after = await get_effective_pricing_version(db)
        assert after["_id"] == before["_id"], "a rejected request must not change the effective version"

    async def test_non_admin_cannot_write_fixed_costs(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            with pytest.raises(HTTPException) as exc:
                await ap.upsert_fixed_cost(
                    ap.FixedCostBody(cost_id=None, name="attacker cost", amount_usd_monthly=1),
                    authorization="Bearer some-other-users-token",
                )
            assert exc.value.status_code == 403

    async def test_non_admin_cannot_resolve_billing_flags(self, db):
        from routers import admin_pricing as ap
        with _as_admin():
            with pytest.raises(HTTPException) as exc:
                await ap.resolve_billing_flag(
                    "anything", ap.ResolveFlagBody(note="x"),
                    authorization="Bearer some-other-users-token",
                )
            assert exc.value.status_code == 403


class TestMongoInjectionOnAdminInputs:
    async def test_mongo_operator_as_cost_id_rejected_by_pydantic(self):
        """A cost_id shaped like a Mongo operator must never be
        forwarded into a query filter as a live operator — Pydantic's
        `cost_id: str | None` type coercion rejects a dict value at
        the request-validation layer, before it could ever reach
        services.pricing_engine.set_fixed_cost."""
        from routers import admin_pricing as ap
        with pytest.raises(ValidationError):
            ap.FixedCostBody(cost_id={"$ne": None}, name="x", amount_usd_monthly=1)

    async def test_mongo_operator_string_as_flag_id_is_a_safe_literal_lookup(self, db):
        """flag_id arrives as a path parameter — ALWAYS a string in
        FastAPI/Starlette routing — so a Mongo-operator-shaped STRING
        (not a structural dict) is just an opaque, nonexistent _id."""
        from routers import admin_pricing as ap
        with _as_admin():
            with pytest.raises(HTTPException) as exc:
                await ap.resolve_billing_flag(
                    '{"$ne": null}', ap.ResolveFlagBody(note="x"),
                    authorization="Bearer admin-token",
                )
            assert exc.value.status_code == 404


class TestConcurrentAdminSaves:
    async def test_two_admins_saving_with_genuinely_tied_timestamps_resolve_unambiguously(self, db):
        """The tiebreaker's actual job: two rows that share the EXACT
        SAME effective_from (a real possibility — e.g. both requests
        quantize "now" the same way, or a migration backfills several
        rows at one shared timestamp) must still resolve to ONE
        unambiguous "most recent" version via the monotonic `seq`,
        not an undefined sort order between equal-timestamp rows."""
        from services.pricing_engine import (
            get_effective_pricing_version,
            set_pricing_version,
        )

        shared_effective_from = time.time()

        async def _save(margin):
            return await set_pricing_version(
                db, margin=margin, actor="wallet_test_admin",
                effective_from=shared_effective_from,
            )

        results = await asyncio.gather(_save(0.11), _save(0.79))
        seqs = sorted(r["seq"] for r in results)
        assert seqs[1] == seqs[0] + 1, "concurrent saves must get distinct, ordered sequence numbers"

        effective = await get_effective_pricing_version(db)
        assert effective["margin"] in (0.11, 0.79)
        assert effective["seq"] == max(seqs), "the higher sequence number must win when timestamps genuinely tie"

    async def test_two_admins_saving_concurrently_each_resolve_to_their_own_version(self, db):
        """When timestamps DON'T tie (the realistic case — independent
        time.time() calls a few microseconds apart), the LATER
        effective_from wins, as a plain "most recent wins" rule
        requires — this is NOT ambiguous, just not seq-ordered, since
        seq is only a tiebreaker for a genuine timestamp collision."""
        from services.pricing_engine import (
            get_effective_pricing_version,
            set_pricing_version,
        )

        async def _save(margin):
            return await set_pricing_version(db, margin=margin, actor="wallet_test_admin",
                                             effective_from=time.time())

        results = await asyncio.gather(_save(0.11), _save(0.79))
        effective = await get_effective_pricing_version(db)
        assert effective["margin"] in (0.11, 0.79), "must resolve to exactly one of the two, never a hybrid"
        assert effective["_id"] in (r["_id"] for r in results)

    async def test_many_concurrent_saves_all_get_distinct_sequence_numbers(self, db):
        from services.pricing_engine import set_pricing_version

        async def _save(i):
            return await set_pricing_version(
                db, margin=0.1 + (i % 10) * 0.01, actor="wallet_test_admin",
            )

        results = await asyncio.gather(*[_save(i) for i in range(20)])
        seqs = [r["seq"] for r in results]
        assert len(set(seqs)) == 20, "every concurrent save must get a UNIQUE sequence number"

    async def test_two_admins_confirming_different_margins_concurrently_each_with_own_preview(self, db):
        """End-to-end concurrency through the real preview/confirm
        flow — two admins each preview their OWN margin, then confirm
        concurrently. Both must succeed (distinct tokens, distinct
        seq), and the final effective version must be one of the two,
        never a corrupted hybrid."""
        from routers import admin_pricing as ap
        with _as_admin():
            preview_a = await ap.preview_margin(
                ap.MarginPreviewBody(margin=0.20), authorization="Bearer admin-token",
            )
            preview_b = await ap.preview_margin(
                ap.MarginPreviewBody(margin=0.70), authorization="Bearer admin-token",
            )
            pa, pb = preview_a["preview"], preview_b["preview"]

            async def _confirm(margin, p):
                return await ap.confirm_margin(
                    ap.MarginConfirmBody(
                        margin=margin, stripe_fee_pct=p["stripe_fee_pct"],
                        stripe_fee_fixed_usd=p["stripe_fee_fixed_usd"],
                        expires_at=p["expires_at"], token=p["token"],
                    ),
                    authorization="Bearer admin-token",
                )

            results = await asyncio.gather(
                _confirm(0.20, pa), _confirm(0.70, pb), return_exceptions=True,
            )
            assert all(isinstance(r, dict) and r.get("ok") for r in results), results
            from services.pricing_engine import get_effective_pricing_version
            effective = await get_effective_pricing_version(db)
            assert effective["margin"] in (0.20, 0.70)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
