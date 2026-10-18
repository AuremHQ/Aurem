"""
tests/test_regression_f1_defect1_name_xss_2026_02.py — F1 defect 1 (2026-02)

Covers:
  - the unclosed-tag payload that bypassed the Q2 sanitizer
  - the 2 missed write paths (auth.py verification-email dispatch,
    team_management.py accept_invite)
  - rendered-template escaping at all 9 HTML render sites
  - the admin broadcast {{name}} substitution
  - the DRY-RUN-only unsanitized-names report (zero writes, states
    which DB it ran against)
"""
from __future__ import annotations

import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.signup_guards import sanitize_display_name

UNCLOSED_PAYLOAD = "<img src=x onerror=alert(1)"
CLOSED_PAYLOAD = "<img src=x onerror=alert(1)>"
SCRIPT_PAYLOAD = "<script>alert(1)</script>"


# ── 1c. Hardened sanitizer ────────────────────────────────────────────
def test_unclosed_tag_now_stripped():
    out = sanitize_display_name(UNCLOSED_PAYLOAD)
    assert "<" not in out
    assert ">" not in out


def test_closed_tag_still_stripped():
    assert sanitize_display_name(CLOSED_PAYLOAD) == ""


def test_script_tag_leaves_no_angle_brackets():
    out = sanitize_display_name(SCRIPT_PAYLOAD)
    assert "<" not in out and ">" not in out


# ── 1b. The 2 missed write paths ─────────────────────────────────────
def test_auth_verification_email_dispatch_sanitizes_name():
    import routers.auth as auth_mod
    src = inspect.getsource(auth_mod)
    # The verification-email dispatch dict must use the sanitizer, not
    # the raw body.name, as its "name" value.
    assert 'sanitize_display_name(body.name)' in src


def test_team_management_accept_invite_sanitizes_name():
    import routers.team_management as team_mod
    src = inspect.getsource(team_mod.accept_invite)
    assert "sanitize_display_name" in src
    assert "body.name or row" not in src  # the old raw-fallback pattern


# ── 1a. Render-time escaping — all 9 sites ───────────────────────────
def test_verification_email_escapes_malicious_name():
    from services.verification_email import render_html
    out = render_html({"name": SCRIPT_PAYLOAD, "email": "a@x.com"}, "tok123")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_welcome_email_escapes_malicious_name():
    from services.welcome_email import render_html
    out = render_html({"name": SCRIPT_PAYLOAD, "email": "a@x.com", "user_id": "u1"})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_onboarding_email_escapes_malicious_name():
    from services.onboarding_email import render_html
    out = render_html({"name": SCRIPT_PAYLOAD, "email": "a@x.com", "user_id": "u1"})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_winback_campaign_escapes_malicious_name():
    from services.winback_campaign import render_html
    out = render_html({"name": SCRIPT_PAYLOAD, "email": "a@x.com"})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


@pytest.mark.asyncio
async def test_funnel_nudge_cron_escapes_malicious_name():
    from services.funnel_nudge_cron import render_html
    db = MagicMock()
    with patch("services.funnel_nudge_cron.magic_click_url", AsyncMock(return_value="http://x/click")), \
         patch("services.funnel_nudge_cron.unsub_url", lambda e: "http://x/unsub", create=True), \
         patch("services.email_link_tokens.unsub_url", lambda e: "http://x/unsub"):
        out = await render_html(db, {"name": SCRIPT_PAYLOAD, "email": "a@x.com", "user_id": "u1"},
                                 "stage1_github_started")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


@pytest.mark.asyncio
async def test_gate_stuck_nudge_escapes_malicious_name():
    from services.gate_stuck_nudge import render_html
    db = MagicMock()
    with patch("services.funnel_nudge_cron.magic_click_url", AsyncMock(return_value="http://x/click")), \
         patch("services.email_link_tokens.unsub_url", lambda e: "http://x/unsub"):
        out = await render_html(db, {"name": SCRIPT_PAYLOAD, "email": "a@x.com", "user_id": "u1"})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_payment_recovery_failure_email_escapes_malicious_name():
    from services.payment_recovery_email import render_html
    out = render_html(
        {"name": SCRIPT_PAYLOAD, "email": "a@x.com"},
        plan="pro", amount_due=29.0, portal_url="http://x/portal",
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_payment_recovery_recovered_email_escapes_malicious_name():
    from services.payment_recovery_email import render_recovered_html
    out = render_recovered_html({"name": SCRIPT_PAYLOAD, "email": "a@x.com"}, plan="pro")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


@pytest.mark.asyncio
async def test_admin_broadcast_escapes_name_and_email_in_template():
    import routers.admin_users as admin_mod
    src = inspect.getsource(admin_mod)
    assert "html_escape(name)" in src
    assert '.replace("{{name}}", name)' not in src


# ── 1d. DRY-RUN unsanitized-names report ─────────────────────────────
class _Cursor:
    def __init__(self, rows):
        self._rows = rows
    def limit(self, n):
        return self
    async def to_list(self, n):
        return self._rows


@pytest.mark.asyncio
async def test_dry_run_flags_mismatched_names_and_proposes_fix():
    from services.name_integrity_audit import run_dry_run_audit
    db = MagicMock()
    db.dev_users.find = MagicMock(return_value=_Cursor([
        {"user_id": "u1", "email": "a@x.com", "name": SCRIPT_PAYLOAD},
        {"user_id": "u2", "email": "b@x.com", "name": "Clean Name"},
    ]))
    result = await run_dry_run_audit(db)
    assert result["dry_run"] is True
    assert result["applied_any_change"] is False
    assert result["mismatched_count"] == 1
    assert result["users"][0]["user_id"] == "u1"
    assert result["users"][0]["proposed_sanitized_name"] == "alert(1)"  # tags stripped, text kept


@pytest.mark.asyncio
async def test_dry_run_states_which_db_it_ran_against(monkeypatch):
    from services.name_integrity_audit import run_dry_run_audit
    monkeypatch.setenv("DB_NAME", "some_other_db")
    monkeypatch.setenv("MONGO_URL", "mongodb://example-host:27017")
    db = MagicMock()
    db.dev_users.find = MagicMock(return_value=_Cursor([]))
    result = await run_dry_run_audit(db)
    assert result["ran_against"]["db_name"] == "some_other_db"
    assert result["ran_against"]["mongo_host"] == "example-host"


@pytest.mark.asyncio
async def test_dry_run_never_writes_anything():
    from services.name_integrity_audit import run_dry_run_audit
    db = MagicMock()
    db.dev_users.find = MagicMock(return_value=_Cursor(
        [{"user_id": "u1", "email": "a@x.com", "name": SCRIPT_PAYLOAD}]
    ))
    await run_dry_run_audit(db)
    db.dev_users.update_one.assert_not_called()
    db.dev_users.insert_one.assert_not_called()
    db.dev_users.delete_one.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_endpoint_is_admin_gated():
    import routers.admin_users as admin_mod
    paths = [r.path for r in admin_mod.router.routes]
    assert "/admin/data-integrity/unsanitized-names-dry-run" in paths
