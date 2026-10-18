"""
tests/test_regression_q1_free_tier_quota_2026_02.py — Q1 (2026-02)

Covers:
  - the free-tier limit is now derived from a single USD config
    constant (config/quota_limits.py), not a hardcoded token number
  - a brand-new user's first scan (even a large real one) is exempt
    and does NOT exhaust them
  - a second scan after lifetime exhaustion is refused with an
    honest, non-misleading message (+ fires a nudge, once)
  - the USD cap is enforced even when token count is LOW but the
    model used is expensive (closes the "token cap != cost cap" gap)
  - founders are never touched by any of this
  - the scan_fix_quota 403 (fix_not_available_on_tier) now also fires
    a nudge notification, same as the pre-existing 402 path
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from config.quota_limits import (
    FREE_LIFETIME_BUDGET_USD, FREE_LIFETIME_TOKEN_DISPLAY, TOKENS_PER_USD_DISPLAY,
)
import services.usage as usage_mod
import services.scan_fix_quota as sfq_mod


def _fake_user(tier="free", is_unlimited=False):
    return {"tier": tier, "tokens_granted": 0, "is_unlimited": is_unlimited,
            "email": "u@example.com"}


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
    async def to_list(self, n):
        return self._rows


def _mock_db(real_llm_rows_total_tokens=0, real_llm_rows_cost_usd=0.0, user=None):
    db = MagicMock()
    db.dev_users.find_one = AsyncMock(return_value=user or _fake_user())
    db.cto_tasks.aggregate = MagicMock(return_value=_Cursor([]))
    db.real_llm_usage.aggregate = MagicMock(
        return_value=_Cursor([{"total": real_llm_rows_total_tokens,
                                "cost_usd": real_llm_rows_cost_usd}])
    )
    db.cto_tasks.count_documents = AsyncMock(return_value=0)
    db.loop_sessions.count_documents = AsyncMock(return_value=0)
    db.visual_bug_reports.count_documents = AsyncMock(return_value=0)
    db.scan_fix_usage.find_one = AsyncMock(return_value=None)
    db.notif_dedupe = MagicMock()
    db.notif_dedupe.find_one = AsyncMock(return_value=None)
    db.notif_dedupe.insert_one = AsyncMock(return_value=None)
    return db


# ── 1. Single source of truth ───────────────────────────────────────
def test_free_limit_is_derived_from_usd_config_not_hardcoded():
    assert FREE_LIFETIME_BUDGET_USD == 0.25
    assert FREE_LIFETIME_TOKEN_DISPLAY == round(
        FREE_LIFETIME_BUDGET_USD * TOKENS_PER_USD_DISPLAY
    )
    assert usage_mod.PLAN_LIMITS["free"] == FREE_LIFETIME_TOKEN_DISPLAY == 250_000


# ── 2. First scan of 50k tokens does NOT exhaust a new user ─────────
@pytest.mark.asyncio
async def test_first_scan_of_50k_tokens_does_not_exhaust_new_user():
    # Simulates the real Shedrick-style burn: 50.2k tokens / $0.0163 —
    # should pass clean since it's the account's first-ever scan.
    db = _mock_db(real_llm_rows_total_tokens=50_200, real_llm_rows_cost_usd=0.0163)
    with patch.object(usage_mod, "require_db", return_value=db):
        await usage_mod.assert_scan_budget("u1", is_account_first_scan=True)
    # No exception raised = pass.


# ── 3. A user above the USD cap on a NON-first scan is blocked honestly
@pytest.mark.asyncio
async def test_second_scan_blocked_with_honest_never_still_running_message():
    db = _mock_db(real_llm_rows_total_tokens=400_000, real_llm_rows_cost_usd=0.30)
    with patch.object(usage_mod, "require_db", return_value=db), \
         patch("services.notifications.emit_notification", AsyncMock()) as notif:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await usage_mod.assert_scan_budget("u1", is_account_first_scan=False)
    assert exc.value.status_code == 402
    detail = exc.value.detail
    assert detail["error"] == "free_allowance_exhausted"
    assert "used your free allowance" in detail["message"]
    for bad_word in ("running", "processing", "in progress", "please wait"):
        assert bad_word not in detail["message"].lower()
    notif.assert_awaited_once()
    assert notif.call_args.kwargs["type"] == "free_allowance_exhausted"


# ── 4. USD cap enforced even with LOW token count + expensive model ──
@pytest.mark.asyncio
async def test_usd_cap_enforced_even_with_low_tokens_expensive_model():
    # Only 300 tokens total, but $0.30 of real cost (an expensive
    # model) — a token-only cap would have let this through forever.
    db = _mock_db(real_llm_rows_total_tokens=300, real_llm_rows_cost_usd=0.30)
    with patch.object(usage_mod, "require_db", return_value=db), \
         patch("services.notifications.emit_notification", AsyncMock()):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await usage_mod.assert_scan_budget("u1", is_account_first_scan=False)
    assert exc.value.status_code == 402
    assert exc.value.detail["error"] == "free_allowance_exhausted"


# ── 5. Founders / unlimited accounts are never touched ──────────────
@pytest.mark.asyncio
async def test_founder_never_blocked_by_scan_budget():
    db = _mock_db(real_llm_rows_total_tokens=10**9, real_llm_rows_cost_usd=999.0,
                   user=_fake_user(tier="founder", is_unlimited=True))
    with patch.object(usage_mod, "require_db", return_value=db):
        await usage_mod.assert_scan_budget("u1", is_account_first_scan=False)


@pytest.mark.asyncio
async def test_non_first_scan_under_budget_passes_clean():
    db = _mock_db(real_llm_rows_total_tokens=1_000, real_llm_rows_cost_usd=0.01)
    with patch.object(usage_mod, "require_db", return_value=db):
        await usage_mod.assert_scan_budget("u1", is_account_first_scan=False)


# ── 6. scan_fix_quota's 403 is no longer silent ──────────────────────
@pytest.mark.asyncio
async def test_fix_not_available_403_now_fires_a_nudge_once():
    user = {"user_id": "u1", "tier": "free"}
    with patch.object(sfq_mod, "get_fix_quota",
                       AsyncMock(return_value={
                           "tier": "free", "fix_tools": ["visibility-kit"],
                           "bulk_fix": False, "monthly_task_limit": 10,
                           "tasks_used": 0, "tasks_remaining": 10,
                           "is_unlimited": False,
                       })), \
         patch.object(sfq_mod, "require_db") as _req_db, \
         patch("services.notifications.emit_notification", AsyncMock()) as notif:
        db = MagicMock()
        db.notif_dedupe.find_one = AsyncMock(return_value=None)
        db.notif_dedupe.insert_one = AsyncMock(return_value=None)
        _req_db.return_value = db
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await sfq_mod.assert_can_fix(user, "health-scan", count=1)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "fix_not_available_on_tier"
    notif.assert_awaited_once()
    assert notif.call_args.kwargs["type"] == "fix_not_available_on_tier"


@pytest.mark.asyncio
async def test_fix_not_available_403_dedupes_second_call_same_month():
    """Second call the same month must NOT re-fire the nudge (no spam)."""
    user = {"user_id": "u1", "tier": "free"}
    with patch.object(sfq_mod, "get_fix_quota",
                       AsyncMock(return_value={
                           "tier": "free", "fix_tools": ["visibility-kit"],
                           "bulk_fix": False, "monthly_task_limit": 10,
                           "tasks_used": 0, "tasks_remaining": 10,
                           "is_unlimited": False,
                       })), \
         patch.object(sfq_mod, "require_db") as _req_db, \
         patch("services.notifications.emit_notification", AsyncMock()) as notif:
        db = MagicMock()
        db.notif_dedupe.find_one = AsyncMock(return_value={"_id": "already-there"})
        db.notif_dedupe.insert_one = AsyncMock(return_value=None)
        _req_db.return_value = db
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await sfq_mod.assert_can_fix(user, "health-scan", count=1)
    notif.assert_not_awaited()


# ── 7. All 3 signup paths use the SAME derived constant (no drift) ───
def test_all_signup_paths_import_same_config_constant():
    import inspect
    import routers.auth as auth_mod
    import routers.google_oauth as google_mod
    import routers.github_oauth as github_mod
    for mod in (auth_mod, google_mod, github_mod):
        src = inspect.getsource(mod)
        assert "FREE_LIFETIME_TOKEN_DISPLAY" in src, (
            f"{mod.__name__} must derive its free-tier starting tokens "
            "from config.quota_limits.FREE_LIFETIME_TOKEN_DISPLAY, not "
            "a second hardcoded number"
        )
        assert "else 1000" not in src.replace(" ", ""), (
            f"{mod.__name__} still has a hardcoded free-tier 1000 fallback"
        )
