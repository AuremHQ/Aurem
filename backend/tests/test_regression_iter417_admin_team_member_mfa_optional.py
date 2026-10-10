"""Admin Team members no longer need a 2FA session (owner decision 2026-10).

Hermetic: drives the real `_finish_admin_check` with only its DB-backed
collaborators stubbed (no Mongo), so it runs everywhere. The Mongo-backed
end-to-end version lives in test_regression_iter_adminteam_s2_enforcement.py.
"""
import sys
import types

import pytest
from fastapi import HTTPException

import cto_services.auth as auth

pytestmark = pytest.mark.real_admin_gate

_MEMBER = {"user_id": "u1", "email": "member@example.test", "mfa": False, "_row_lookup": "found"}
_USERS_ROUTE = ("GET", "/api/aurem-dev/admin/users")


@pytest.fixture
def gate(monkeypatch):
    usage = types.ModuleType("services.usage")
    usage.is_founder_email = lambda email: False
    team = types.ModuleType("services.admin_team")

    async def get_active_member(user_id):
        return {"user_id": user_id}

    async def effective_level(member, screen_key):
        return 99

    team.get_active_member = get_active_member
    team.effective_level = effective_level
    monkeypatch.setitem(sys.modules, "services.usage", usage)
    monkeypatch.setitem(sys.modules, "services.admin_team", team)
    monkeypatch.setattr(auth, "_matched_route", lambda request: _USERS_ROUTE)
    monkeypatch.delenv("ADMIN_TEAM_REQUIRE_MFA", raising=False)


@pytest.mark.asyncio
async def test_regression_iter417_member_without_mfa_claim_is_allowed_by_default(gate):
    user = dict(_MEMBER)
    assert await auth._finish_admin_check(user) is user


@pytest.mark.asyncio
async def test_regression_iter417_opt_in_restores_the_2fa_requirement(gate, monkeypatch):
    monkeypatch.setenv("ADMIN_TEAM_REQUIRE_MFA", "1")
    with pytest.raises(HTTPException) as exc:
        await auth._finish_admin_check(dict(_MEMBER))
    assert exc.value.status_code == 403 and "2FA" in exc.value.detail


@pytest.mark.asyncio
async def test_regression_iter417_opt_in_still_admits_a_verified_member(gate, monkeypatch):
    monkeypatch.setenv("ADMIN_TEAM_REQUIRE_MFA", "1")
    user = dict(_MEMBER, mfa=True)
    assert await auth._finish_admin_check(user) is user


@pytest.mark.asyncio
async def test_regression_iter417_disabling_2fa_still_needs_a_verified_session(gate, monkeypatch):
    monkeypatch.setattr(auth, "_matched_route", lambda request: ("POST", "/api/aurem-dev/admin/2fa/disable"))
    with pytest.raises(HTTPException) as exc:
        await auth._finish_admin_check(dict(_MEMBER))
    assert exc.value.status_code == 403 and "2FA" in exc.value.detail
