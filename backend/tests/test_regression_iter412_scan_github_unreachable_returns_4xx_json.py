"""
tests/test_regression_iter412_scan_github_unreachable_returns_4xx_json.py

UI-fix C6 (2026-10) — a 401/403/404 while minting the GitHub App
installation token (GithubAppAuthError code "github_rejected") used to
surface as a 502. Cloudflare replaces 502 response bodies with its own
"Bad gateway" HTML on prod, so the frontend's actionable error message
never reached the user. This is now a 409 with a stable JSON body
{"code": "github_install_unreachable", "detail": "..."} instead.
"""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_scan_github_rejected_returns_409_with_stable_code():
    from routers import codebase_health
    from services.pat_vault import GithubAppAuthError
    from fastapi import HTTPException

    fake_db = AsyncMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value={
        "github_owner": "acme", "github_repo": "widgets", "user_id": "u1",
    })

    async def _raise_rejected(proj):
        raise GithubAppAuthError(
            "github_rejected",
            "GitHub returned HTTP 404 while minting the App installation token. Retry shortly.",
        )

    with patch("routers.codebase_health.current_dev",
               new=AsyncMock(return_value={"user_id": "u1", "is_admin": True})), \
         patch("routers.codebase_health.get_db", return_value=fake_db), \
         patch("services.pat_vault.get_repo_token", side_effect=_raise_rejected):
        with pytest.raises(HTTPException) as exc_info:
            await codebase_health.scan(
                {"project_id": "p1", "categories": ["code_quality"]},
                authorization="Bearer fake",
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "github_install_unreachable"
        assert "404" in exc_info.value.detail["detail"]


@pytest.mark.asyncio
async def test_scan_github_unreachable_still_returns_502():
    """Genuine network unreachability (not a GitHub 401/403/404 rejection)
    is a different failure mode and keeps its original 502 — only the
    github_rejected case (real GitHub response) changed in this fix."""
    from routers import codebase_health
    from services.pat_vault import GithubAppAuthError
    from fastapi import HTTPException

    fake_db = AsyncMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value={
        "github_owner": "acme", "github_repo": "widgets", "user_id": "u1",
    })

    async def _raise_unreachable(proj):
        raise GithubAppAuthError("github_unreachable", "GitHub is temporarily unreachable. Retry shortly.")

    with patch("routers.codebase_health.current_dev",
               new=AsyncMock(return_value={"user_id": "u1", "is_admin": True})), \
         patch("routers.codebase_health.get_db", return_value=fake_db), \
         patch("services.pat_vault.get_repo_token", side_effect=_raise_unreachable):
        with pytest.raises(HTTPException) as exc_info:
            await codebase_health.scan(
                {"project_id": "p1", "categories": ["code_quality"]},
                authorization="Bearer fake",
            )
        assert exc_info.value.status_code == 502
