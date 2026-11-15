"""tests/test_regression_iter2026_23a_plan_transition_per_tier_rate.py

ITEM 9 (independent review) — fix round.

CURRENT RULE (before this fix), read from config/plan_transition.py
and services/plan_transition.py::classify_user: a single flat
LEGACY_TASK_TO_TOKEN_RATE (float, None by default) applied uniformly
to every paying tier's `remaining_tasks` — if unset, EVERY paying
user is marked `needs_decision: True` with no proposed grant, no
matter how knowable a fair rate actually is for their specific tier.

THIS FIX derives a DEFAULT per-tier rate —
`tokens_per_old_task(tier) = price_monthly * USD_TO_TOKENS / tasks_per_month`
— read from services.subscription_tiers.TIER_LIMITS (never
hardcoded), so a paying user on a real, priced, capped tier
(starter/pro/team) gets a real proposed grant BY DEFAULT, with no
founder action required. LEGACY_TASK_TO_TOKEN_RATE stays as an
OPTIONAL override: when set, it replaces the per-tier math for every
row and the report's `summary` carries `override_active: True`.

Tiers with no price (free/founder) or no finite task cap (team's own
unlimited concepts don't apply here — tasks_per_month IS finite for
team, so this only matters for `founder`, which never reaches this
code path anyway since admin/founder accounts are excluded earlier in
classify_user) get `needs_decision: True` and a clear reason string,
never a guessed number — proven directly against the new
`_tokens_per_old_task_micro` helper, not just through classify_user's
narrower live code paths.

paid_cancelled users' CURRENT `tier` field reads "free" (already
downgraded by the cancellation webhook) — their former PAID tier is
not recoverable from any field this codebase stores anywhere
(grepped: no last_paid_tier/previous_tier/former_tier/tier_before
field exists). Rather than guess, this is its own
`needs_decision: True` reason, UNLESS the override env is set (which
applies uniformly regardless of former tier, same as before).

Rounding: per-task micro-token rate is computed with math.floor at
WALLET_MICRO_PER_TOKEN granularity (never rounded up — "no free
tokens").
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

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
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})


async def _mk_user(db, **fields):
    u = _uid(fields.pop("_tag", "user"))
    doc = {"user_id": u, "tier": "free", "email": f"{u}@example.com", **fields}
    await db.dev_users.insert_one(doc)
    return u


class TestTokensPerOldTaskMicroHelper:
    """Direct unit tests of the derivation formula, independent of
    classify_user's narrower live call sites (free/founder tiers
    never reach the paid-grant branch in practice, but the helper
    itself must still be provably correct and non-guessing for them)."""

    def test_starter_matches_worked_example(self):
        from services.plan_transition import tokens_per_old_task_micro
        micro, reason = tokens_per_old_task_micro("starter")
        # 9 * 100 / 50 = 18.0 tokens/task exactly -> 18000 micro-tokens
        assert micro == 18000
        assert reason == ""

    def test_pro_matches_worked_example_floored(self):
        from services.plan_transition import tokens_per_old_task_micro
        micro, reason = tokens_per_old_task_micro("pro")
        # 19 * 100 / 300 = 6.3333... -> floor(6333.33...) = 6333 micro-tokens
        assert micro == 6333
        assert reason == ""

    def test_team_matches_worked_example(self):
        from services.plan_transition import tokens_per_old_task_micro
        micro, reason = tokens_per_old_task_micro("team")
        # 49 * 100 / 400 = 12.25 -> 12250 micro-tokens exactly
        assert micro == 12250
        assert reason == ""

    def test_free_tier_returns_none_with_clear_reason_never_a_guess(self):
        from services.plan_transition import tokens_per_old_task_micro
        micro, reason = tokens_per_old_task_micro("free")
        assert micro is None
        assert reason and "price" in reason.lower()

    def test_founder_tier_returns_none_with_clear_reason_never_a_guess(self):
        from services.plan_transition import tokens_per_old_task_micro
        micro, reason = tokens_per_old_task_micro("founder")
        assert micro is None
        assert reason

    def test_unknown_tier_string_returns_none_not_a_crash(self):
        from services.plan_transition import tokens_per_old_task_micro
        micro, reason = tokens_per_old_task_micro("not_a_real_tier")
        assert micro is None
        assert reason

    def test_values_are_read_from_subscription_tiers_not_hardcoded(self, monkeypatch):
        """Change the source of truth and confirm the derivation
        follows it — proves this isn't a hardcoded 18/6.33/12.25."""
        import services.subscription_tiers as st
        from services.plan_transition import tokens_per_old_task_micro
        patched = dict(st.TIER_LIMITS[st.Tier.STARTER])
        patched["price_monthly"] = 100
        patched["tasks_per_month"] = 100
        monkeypatch.setitem(st.TIER_LIMITS, st.Tier.STARTER, patched)
        micro, _ = tokens_per_old_task_micro("starter")
        # 100 * 100 / 100 = 100.0 tokens/task -> 100000 micro-tokens
        assert micro == 100000


class TestClassifyUserDefaultPerTierGrant:
    async def test_paid_active_starter_gets_real_grant_by_default_no_override_needed(self, db, monkeypatch):
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", None)
        from services.plan_transition import classify_user
        u = await _mk_user(
            db, _tag="paidactive", tier="starter", stripe_sub_id="sub_abc",
            fix_entitlement=True, tokens_granted=0,
        )
        user = await db.dev_users.find_one({"user_id": u})
        result = await classify_user(db, user)
        assert result["segment"] == "paid_active"
        assert result["needs_decision"] is False
        # Fresh user, 0 tasks used this month -> remaining_tasks == 50 (starter cap)
        # 50 * 18 tokens/task = 900 — matches PLAN_TOKENS["starter"] in
        # frontend/src/lib/billingCopy.js exactly (same $9 grant, same
        # 100 tokens/USD rate), confirming the formula is self-consistent
        # with the rest of the system, not a coincidence.
        assert result["proposed_token_grant"] == 900
        assert result.get("override_active") is not True

    async def test_paid_active_pro_gets_floored_grant(self, db, monkeypatch):
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", None)
        from services.plan_transition import classify_user
        u = await _mk_user(
            db, _tag="paidpro", tier="pro", stripe_sub_id="sub_pro",
            fix_entitlement=True, tokens_granted=0,
        )
        user = await db.dev_users.find_one({"user_id": u})
        result = await classify_user(db, user)
        assert result["needs_decision"] is False
        # 300 remaining tasks * 6333 micro-tokens/task = 1,899,900 micro
        # -> 1899.9 tokens, never rounded up past the true floored value.
        assert result["proposed_token_grant"] == pytest.approx(1899.9)

    async def test_paid_cancelled_former_tier_unknown_needs_decision(self, db, monkeypatch):
        """tier is already 'free' (downgraded by the cancellation
        webhook) — the former PAID tier cannot be recovered from any
        stored field, so this must be needs_decision, not a guess."""
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", None)
        from services.plan_transition import classify_user
        u = await _mk_user(
            db, _tag="paidcancelled", tier="free", stripe_sub_id=None,
            fix_entitlement=True, tier_updated_at="2026-09-01T00:00:00+00:00",
        )
        user = await db.dev_users.find_one({"user_id": u})
        result = await classify_user(db, user)
        assert result["segment"] == "paid_cancelled"
        assert result["needs_decision"] is True
        assert result["proposed_token_grant"] is None
        assert "former" in result.get("grant_reason", "").lower() or "tier" in result.get("grant_reason", "").lower()


class TestOverrideEnvStillWorksAndIsFlagged:
    async def test_override_set_applies_uniformly_and_flags_override_active(self, db, monkeypatch):
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", 20.0)
        from services.plan_transition import classify_user
        u = await _mk_user(
            db, _tag="paidoverride", tier="starter", stripe_sub_id="sub_abc",
            fix_entitlement=True, tokens_granted=0,
        )
        user = await db.dev_users.find_one({"user_id": u})
        result = await classify_user(db, user)
        assert result["needs_decision"] is False
        assert result["proposed_token_grant"] == 1000  # 50 * 20.0
        assert result["override_active"] is True

    async def test_override_set_also_resolves_paid_cancelled_unknown_former_tier(self, db, monkeypatch):
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", 20.0)
        from services.plan_transition import classify_user
        u = await _mk_user(
            db, _tag="cancelledoverride", tier="free", stripe_sub_id=None,
            fix_entitlement=True, tier_updated_at="2026-09-01T00:00:00+00:00",
        )
        user = await db.dev_users.find_one({"user_id": u})
        result = await classify_user(db, user)
        assert result["needs_decision"] is False
        assert result["override_active"] is True

    async def test_run_dry_run_summary_carries_override_active_flag(self, db, monkeypatch):
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", 20.0)
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        u = await _mk_user(db, _tag="summaryoverride", tier="starter", stripe_sub_id="sub_x", fix_entitlement=True)
        guarded = WriteGuardedDB(db)
        report = await run_dry_run(guarded, user_filter={"user_id": u})
        assert report["summary"]["override_active"] is True

    async def test_run_dry_run_summary_override_active_false_when_unset(self, db, monkeypatch):
        import config.plan_transition as cfg
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", None)
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        u = await _mk_user(db, _tag="summarynooverride", tier="starter", stripe_sub_id="sub_x", fix_entitlement=True)
        guarded = WriteGuardedDB(db)
        report = await run_dry_run(guarded, user_filter={"user_id": u})
        assert report["summary"]["override_active"] is False


class TestLosesUnlimitedMaxxFlag:
    async def test_team_user_flagged_true(self, db):
        from services.plan_transition import classify_user
        u = await _mk_user(db, _tag="teamuser", tier="team", stripe_sub_id="sub_t", fix_entitlement=True)
        user = await db.dev_users.find_one({"user_id": u})
        result = await classify_user(db, user)
        assert result["loses_unlimited_maxx"] is True

    async def test_starter_user_flagged_false(self, db):
        from services.plan_transition import classify_user
        u = await _mk_user(db, _tag="starteruser", tier="starter", stripe_sub_id="sub_s", fix_entitlement=True)
        user = await db.dev_users.find_one({"user_id": u})
        result = await classify_user(db, user)
        assert result["loses_unlimited_maxx"] is False

    async def test_free_active_user_flagged_false_read_only_no_behavior_change(self, db):
        from services.plan_transition import classify_user
        u = await _mk_user(db, _tag="freeuser", tier="free", last_login_at=time.time())
        user = await db.dev_users.find_one({"user_id": u})
        result = await classify_user(db, user)
        assert result["loses_unlimited_maxx"] is False
        # Read-only: no other field's behavior changes because of this flag.
        assert result["segment"] == "free_active"


class TestTotalProposedLiabilityNeverExceedsSumOfPriceMonthly:
    async def test_assertion_with_seeded_mixed_tier_fixture(self, db, monkeypatch):
        """For ANY paid user, remaining_tasks <= tasks_per_month by
        construction (remaining = max(0, cap - used)), so
        remaining_tasks/tasks_per_month * price <= price always — the
        floored micro-token rounding only makes this MORE true, never
        less. Proven here with a real seeded mix, not just reasoned
        about, per the task's explicit ask for an assertion test."""
        import config.plan_transition as cfg
        import services.subscription_tiers as st
        from config.token_pricing import tokens_to_usd
        from services.plan_transition import run_dry_run
        from services.plan_transition_write_guard import WriteGuardedDB
        monkeypatch.setattr(cfg, "LEGACY_TASK_TO_TOKEN_RATE", None)

        ids = []
        prices = []
        for tier in ("starter", "pro", "team", "starter", "pro"):
            u = await _mk_user(
                db, _tag=f"liab{tier}", tier=tier, stripe_sub_id=f"sub_{tier}_{uuid.uuid4().hex[:4]}",
                fix_entitlement=True,
            )
            ids.append(u)
            prices.append(st.TIER_LIMITS[st.Tier(tier)]["price_monthly"])

        guarded = WriteGuardedDB(db)
        report = await run_dry_run(guarded, user_filter={"user_id": {"$in": ids}})
        total_proposed_usd = sum(
            tokens_to_usd(r["proposed_token_grant"])
            for r in report["rows"]
            if r["user_id"] in ids and r.get("proposed_token_grant") is not None
        )
        assert total_proposed_usd <= sum(prices), (
            f"proposed liability ${total_proposed_usd} exceeds the sum of "
            f"price_monthly ${sum(prices)} for the included paid users"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
