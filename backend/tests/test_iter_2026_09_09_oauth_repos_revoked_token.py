"""
tests/test_iter_2026_09_09_oauth_repos_revoked_token.py

Founder-reported production incident: GET /github/oauth/repos raised
an UNHANDLED httpx.HTTPStatusError when the stored OAuth token was
stale/revoked (GitHub returned 401 for /user/repos), propagating
through the entire FastAPI/Starlette stack as a raw, ugly 500 with a
full traceback logged in production instead of a clean, actionable
error. Fixed: catch httpx.HTTPStatusError in routers/github_oauth.py's
`repos()` and convert to a clean 409 ("reconnect") or 502 (other
GitHub errors), matching how other error paths in this router already
behave.

2026-09-09b — the reconnect case was FIRST shipped as a plain 401,
which collided with the frontend's global "401 == AUREM session dead,
force logout everywhere" contract (lib/api.js) and force-logged-out
users the instant NewUserWizard's auto-mount check hit a stale legacy
OAuth link. Changed to 409 Conflict so a revoked THIRD-PARTY GitHub
token can never again be confused with an invalid AUREM JWT.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


async def test_revoked_token_returns_clean_401_not_raw_500(monkeypatch):
    import routers.github_oauth as gho

    async def _fake_current_dev(authorization=None):
        return {"user_id": "test_oauth_revoked"}
    monkeypatch.setattr(gho, "current_dev", _fake_current_dev)

    class _FakeDB:
        class dev_users:
            @staticmethod
            async def find_one(*a, **k):
                return {"github": {"access_token": "stale-token-123"}}
    monkeypatch.setattr(gho, "get_db", lambda: _FakeDB())

    async def _fake_gh_repos(token):
        req = httpx.Request("GET", "https://api.github.com/user/repos")
        resp = httpx.Response(401, request=req, json={"message": "Bad credentials"})
        raise httpx.HTTPStatusError("401", request=req, response=resp)
    monkeypatch.setattr(gho, "gh_repos", _fake_gh_repos)

    with pytest.raises(HTTPException) as exc_info:
        await gho.repos(authorization="Bearer x")

    # 409, NOT 401 — a revoked GitHub OAuth token must never collide
    # with the AUREM-session-401 contract the frontend force-logs-out
    # on (see 2026-09-09b comment above the raise site).
    assert exc_info.value.status_code == 409
    assert exc_info.value.status_code != 401
    assert "reconnect" in exc_info.value.detail.lower()


async def test_other_github_error_returns_clean_502_not_raw_500(monkeypatch):
    import routers.github_oauth as gho

    async def _fake_current_dev(authorization=None):
        return {"user_id": "test_oauth_502"}
    monkeypatch.setattr(gho, "current_dev", _fake_current_dev)

    class _FakeDB:
        class dev_users:
            @staticmethod
            async def find_one(*a, **k):
                return {"github": {"access_token": "valid-token"}}
    monkeypatch.setattr(gho, "get_db", lambda: _FakeDB())

    async def _fake_gh_repos(token):
        req = httpx.Request("GET", "https://api.github.com/user/repos")
        resp = httpx.Response(503, request=req, json={"message": "Service unavailable"})
        raise httpx.HTTPStatusError("503", request=req, response=resp)
    monkeypatch.setattr(gho, "gh_repos", _fake_gh_repos)

    with pytest.raises(HTTPException) as exc_info:
        await gho.repos(authorization="Bearer x")

    assert exc_info.value.status_code == 502
    # No raw exception text leaked to the client.
    assert "HTTPStatusError" not in exc_info.value.detail
