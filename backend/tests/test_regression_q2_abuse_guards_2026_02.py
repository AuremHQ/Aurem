"""
tests/test_regression_q2_abuse_guards_2026_02.py — Q2 (2026-02)

Covers:
  - per-user daily USD spend cap (honest message, founder bypass,
    independent of the lifetime cap)
  - herclan.com now blocked as a disposable signup domain
  - signup display-name sanitization (HTML stripped, length capped,
    control chars removed) + all 3 signup paths use it
  - platform-wide daily spend alarm (fires once per day into the
    existing topup_alerts/GET /admin/alerts surface, never raises)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import services.usage as usage_mod
import services.platform_spend_alarm as alarm_mod
from services.signup_guards import (
    DISPOSABLE_DOMAINS, sanitize_display_name, _disposable_hit,
)
from config.quota_limits import PER_USER_DAILY_SPEND_CAP_USD, PLATFORM_DAILY_SPEND_ALARM_USD


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
    async def to_list(self, n):
        return self._rows


# ── 1. Per-user daily spend cap ──────────────────────────────────────
@pytest.mark.asyncio
async def test_daily_spend_cap_blocks_with_honest_message():
    db = MagicMock()
    db.dev_users.find_one = AsyncMock(return_value={"tier": "free", "is_unlimited": False})
    db.real_llm_usage.aggregate = MagicMock(
        return_value=_Cursor([{"cost_usd": PER_USER_DAILY_SPEND_CAP_USD + 0.01}])
    )
    db.notif_dedupe = MagicMock()
    db.notif_dedupe.find_one = AsyncMock(return_value=None)
    db.notif_dedupe.insert_one = AsyncMock(return_value=None)
    with patch.object(usage_mod, "require_db", return_value=db), \
         patch("services.notifications.emit_notification", AsyncMock()) as notif:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await usage_mod.assert_daily_spend_cap("u1")
    assert exc.value.status_code == 402
    assert exc.value.detail["error"] == "daily_spend_cap_hit"
    for bad_word in ("running", "processing", "in progress"):
        assert bad_word not in exc.value.detail["message"].lower()
    notif.assert_awaited_once()


@pytest.mark.asyncio
async def test_daily_spend_cap_passes_clean_under_cap():
    db = MagicMock()
    db.dev_users.find_one = AsyncMock(return_value={"tier": "free", "is_unlimited": False})
    db.real_llm_usage.aggregate = MagicMock(return_value=_Cursor([{"cost_usd": 0.01}]))
    with patch.object(usage_mod, "require_db", return_value=db):
        await usage_mod.assert_daily_spend_cap("u1")  # no raise


@pytest.mark.asyncio
async def test_daily_spend_cap_founder_never_blocked():
    db = MagicMock()
    db.dev_users.find_one = AsyncMock(return_value={"tier": "founder", "is_unlimited": True})
    db.real_llm_usage.aggregate = MagicMock(return_value=_Cursor([{"cost_usd": 999.0}]))
    with patch.object(usage_mod, "require_db", return_value=db):
        await usage_mod.assert_daily_spend_cap("u1")  # no raise, short-circuits before query


@pytest.mark.asyncio
async def test_daily_spend_cap_applies_to_paid_tiers_too():
    """Abuse guard, not a plan feature — a 'pro' account hammering
    calls in a burst is still blocked."""
    db = MagicMock()
    db.dev_users.find_one = AsyncMock(return_value={"tier": "pro", "is_unlimited": False})
    db.real_llm_usage.aggregate = MagicMock(
        return_value=_Cursor([{"cost_usd": PER_USER_DAILY_SPEND_CAP_USD + 1.0}])
    )
    db.notif_dedupe = MagicMock()
    db.notif_dedupe.find_one = AsyncMock(return_value=None)
    db.notif_dedupe.insert_one = AsyncMock(return_value=None)
    with patch.object(usage_mod, "require_db", return_value=db), \
         patch("services.notifications.emit_notification", AsyncMock()):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await usage_mod.assert_daily_spend_cap("u1")
    assert exc.value.status_code == 402


# ── 2. Disposable domain list ────────────────────────────────────────
def test_herclan_now_blocked_as_disposable():
    assert "herclan.com" in DISPOSABLE_DOMAINS
    assert _disposable_hit("spammer@herclan.com") is True
    assert _disposable_hit("real.user@gmail.com") is False


# ── 3. Display-name sanitization ─────────────────────────────────────
def test_sanitize_strips_html_tags_entirely():
    assert sanitize_display_name('<img src=x onerror=alert(1)>') == ""
    assert sanitize_display_name("<b>Bob</b> <script>evil()</script>") == "Bob evil()"


def test_sanitize_caps_length_and_collapses_whitespace():
    assert len(sanitize_display_name("A" * 500)) == 80
    assert sanitize_display_name("  John    Doe  ") == "John Doe"


def test_sanitize_strips_control_chars_and_handles_empty():
    assert sanitize_display_name("Bob\x00\x07Doe") == "BobDoe"
    assert sanitize_display_name(None) == ""
    assert sanitize_display_name("") == ""
    assert sanitize_display_name("   ") == ""


def test_all_signup_paths_use_sanitize_display_name():
    import inspect
    import routers.auth as auth_mod
    import routers.google_oauth as google_mod
    import routers.github_oauth as github_mod
    for mod in (auth_mod, google_mod, github_mod):
        assert "sanitize_display_name" in inspect.getsource(mod), (
            f"{mod.__name__} must sanitize the signup display name"
        )


# ── 4. Platform-wide daily spend alarm ───────────────────────────────
@pytest.mark.asyncio
async def test_platform_alarm_fires_once_into_existing_topup_alerts():
    db = MagicMock()
    db.real_llm_usage.aggregate = MagicMock(
        return_value=_Cursor([{"cost_usd": PLATFORM_DAILY_SPEND_ALARM_USD + 1.0}])
    )
    db.topup_alerts.find_one = AsyncMock(return_value=None)
    db.topup_alerts.insert_one = AsyncMock(return_value=None)
    db.topup_alerts.update_one = AsyncMock(return_value=None)
    with patch("services.founder_alerts.send_founder_alert", AsyncMock()):
        result = await alarm_mod.check_and_alert(db)
    assert result["alarmed"] is True
    db.topup_alerts.insert_one.assert_awaited_once()
    inserted = db.topup_alerts.insert_one.call_args.args[0]
    assert inserted["integration_id"] == "platform_daily_spend"
    assert inserted["severity"] == "critical"
    assert inserted["status"] == "active"


@pytest.mark.asyncio
async def test_platform_alarm_silent_under_threshold():
    db = MagicMock()
    db.real_llm_usage.aggregate = MagicMock(return_value=_Cursor([{"cost_usd": 0.50}]))
    db.topup_alerts.insert_one = AsyncMock(return_value=None)
    result = await alarm_mod.check_and_alert(db)
    assert result["alarmed"] is False
    db.topup_alerts.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_alarm_dedupes_same_day():
    db = MagicMock()
    db.real_llm_usage.aggregate = MagicMock(
        return_value=_Cursor([{"cost_usd": PLATFORM_DAILY_SPEND_ALARM_USD + 1.0}])
    )
    db.topup_alerts.find_one = AsyncMock(return_value={"alert_key": "already-exists"})
    db.topup_alerts.insert_one = AsyncMock(return_value=None)
    db.topup_alerts.update_one = AsyncMock(return_value=None)
    result = await alarm_mod.check_and_alert(db)
    assert result["already_fired_today"] is True
    db.topup_alerts.insert_one.assert_not_awaited()
    db.topup_alerts.update_one.assert_awaited_once()  # still bumps last_seen/seen_count


@pytest.mark.asyncio
async def test_platform_alarm_never_raises_on_db_error():
    """A broken alarm check must never break the real LLM call path
    it's fired from (services/loop_token_ledger.py fire-and-forget)."""
    db = MagicMock()
    db.real_llm_usage.aggregate = MagicMock(side_effect=RuntimeError("mongo down"))
    result = await alarm_mod.check_and_alert(db)
    assert result["alarmed"] is False
    assert "error" in result
