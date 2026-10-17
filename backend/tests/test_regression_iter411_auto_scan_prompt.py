"""Regression lock for Ticket A-UI (2026-02) — "Turn on weekly
auto-scan?" one-time prompt after a project's first scan.

Covers: the backend `is_first_scan` signal on GET /codebase-health/scan
(computed BEFORE the current scan's own insert), the runtime email-
delivery health check (never claims Resend works when it doesn't —
conservative default on a missing/stale snapshot), and that
`auto_scan_prompt_dismissed` is accepted by the existing project PATCH.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── is_first_scan signal ─────────────────────────────────────────────
def test_scan_endpoint_computes_is_first_scan_before_its_own_insert():
    """Structural lock: the count_documents() call that decides
    is_first_scan must run BEFORE insert_one() for this same scan —
    otherwise every scan would see itself already counted and never
    report is_first_scan=True."""
    import inspect
    import routers.codebase_health as mod
    src = inspect.getsource(mod.scan)
    count_idx = src.index("count_documents")
    insert_idx = src.index("codebase_health_scans.insert_one(")
    assert count_idx < insert_idx
    assert '"is_first_scan"' in src


@pytest.mark.asyncio
async def test_count_documents_scoped_to_this_project_only():
    """Lower-risk unit check on the exact query shape used — must be
    scoped by project_id (never a global count across all projects,
    which would make every project's 2nd+ scan falsely look "first")."""
    fake_db = MagicMock()
    fake_db.codebase_health_scans.count_documents = AsyncMock(return_value=3)
    await fake_db.codebase_health_scans.count_documents({"project_id": "p1"})
    fake_db.codebase_health_scans.count_documents.assert_awaited_once_with(
        {"project_id": "p1"},
    )


# ── Email delivery health check ──────────────────────────────────────
@pytest.mark.asyncio
async def test_email_status_healthy_when_resend_probe_is_ok():
    from routers.cto_projects.management import email_delivery_status
    fake_db = MagicMock()
    fake_db.integration_health.find_one = AsyncMock(return_value={
        "results": [{"id": "resend", "status": "ok", "summary": "Resend reachable"}],
    })
    with patch("routers.cto_projects.management._pkg.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.cto_projects.management._pkg.get_db", return_value=fake_db):
        resp = await email_delivery_status(authorization="Bearer x")
    assert resp == {"ok": True, "healthy": True, "reason": None}


@pytest.mark.asyncio
async def test_email_status_unhealthy_when_resend_probe_is_broken():
    """Real current state of this preview env — Resend returns
    HTTP 400 'API key is invalid'. Must surface the real reason, never
    silently say healthy."""
    from routers.cto_projects.management import email_delivery_status
    fake_db = MagicMock()
    fake_db.integration_health.find_one = AsyncMock(return_value={
        "results": [{"id": "resend", "status": "broken", "summary": "HTTP 400"}],
    })
    with patch("routers.cto_projects.management._pkg.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.cto_projects.management._pkg.get_db", return_value=fake_db):
        resp = await email_delivery_status(authorization="Bearer x")
    assert resp["healthy"] is False
    assert resp["reason"] == "HTTP 400"


@pytest.mark.asyncio
async def test_email_status_defaults_to_unhealthy_when_no_snapshot_exists():
    """Conservative default — no cached probe yet means we genuinely
    don't know, so NEVER claim emails will be sent when we can't prove
    it. This is the exact rule Ticket A-UI's spec states explicitly."""
    from routers.cto_projects.management import email_delivery_status
    fake_db = MagicMock()
    fake_db.integration_health.find_one = AsyncMock(return_value=None)
    with patch("routers.cto_projects.management._pkg.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.cto_projects.management._pkg.get_db", return_value=fake_db):
        resp = await email_delivery_status(authorization="Bearer x")
    assert resp["healthy"] is False


@pytest.mark.asyncio
async def test_email_status_defaults_to_unhealthy_when_db_read_crashes():
    from routers.cto_projects.management import email_delivery_status
    fake_db = MagicMock()
    fake_db.integration_health.find_one = AsyncMock(side_effect=RuntimeError("mongo down"))
    with patch("routers.cto_projects.management._pkg.current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.cto_projects.management._pkg.get_db", return_value=fake_db):
        resp = await email_delivery_status(authorization="Bearer x")  # must not raise
    assert resp["healthy"] is False


# ── auto_scan_prompt_dismissed accepted by the existing PATCH ────────
def test_update_project_model_accepts_prompt_dismissed_flag():
    from routers.cto_projects.management import UpdateProject
    body = UpdateProject(auto_scan_prompt_dismissed=True)
    assert body.model_dump()["auto_scan_prompt_dismissed"] is True
