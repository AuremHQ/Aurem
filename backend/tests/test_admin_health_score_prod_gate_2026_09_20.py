"""
2026-09-20 — Deploy-health fix, round 5: `deployment_agent` traced
live prod nginx "upstream timed out ... /health" flaps to the
admin-triggered `/admin/health-score/test-coverage/run` action. The
pytest+coverage subprocess it launches is genuinely CPU-heavy (real
fork/exec under the pod's own CPU cgroup quota), and there is no
separate worker pod to relocate it onto, so it competes directly with
the FastAPI process serving `/health` on the same pod.

Fix: refuse the action outright in production (reusing the same
`AUREM_ENV` / MONGO_URL-based environment detection `routers.version.
_env_from_host` already uses elsewhere), so the diagnostic action can
never again flap live health checks for real users. It remains fully
available in preview/dev.

Covers:
  - Production (Atlas MONGO_URL) → 409, background task NOT scheduled.
  - Preview/dev (localhost MONGO_URL) → 200, background task scheduled.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from routers.admin_health_score import trigger_coverage_run


@pytest.mark.asyncio
async def test_coverage_run_blocked_in_production(monkeypatch):
    monkeypatch.setenv("MONGO_URL", "mongodb+srv://user:pw@cluster0.mongodb.net/aurem")
    monkeypatch.delenv("AUREM_ENV", raising=False)

    with patch("services.health_coverage_scan.run_coverage_scan") as mock_run:
        with pytest.raises(HTTPException) as exc_info:
            await trigger_coverage_run()
    assert exc_info.value.status_code == 409
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_coverage_run_allowed_in_preview(monkeypatch):
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017/aurem")
    monkeypatch.delenv("AUREM_ENV", raising=False)

    with patch("routers.admin_health_score.get_db", return_value=MagicMock()):
        with patch("services.health_coverage_scan.is_scan_in_progress",
                   new_callable=AsyncMock, return_value=False):
            with patch("services.health_coverage_scan.run_coverage_scan",
                       new_callable=AsyncMock) as mock_run:
                result = await trigger_coverage_run()
    assert result["status"] == "started"


@pytest.mark.asyncio
async def test_coverage_run_rejected_when_already_in_progress(monkeypatch):
    """2026-09-20 round 6 — the endpoint must also refuse a second
    concurrent trigger (immediate 409) instead of silently returning
    "started" while doing nothing, which is the live bug reported on
    the Admin Cockpit."""
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017/aurem")
    monkeypatch.delenv("AUREM_ENV", raising=False)

    with patch("routers.admin_health_score.get_db", return_value=MagicMock()):
        with patch("services.health_coverage_scan.is_scan_in_progress",
                   new_callable=AsyncMock, return_value=True):
            with patch("services.health_coverage_scan.run_coverage_scan",
                       new_callable=AsyncMock) as mock_run:
                with pytest.raises(HTTPException) as exc_info:
                    await trigger_coverage_run()
    assert exc_info.value.status_code == 409
    mock_run.assert_not_called()
