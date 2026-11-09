"""tests/test_regression_iter2026_61_part2c_b4_auth_me_flag.py

Part 2c B4 — `/auth/me` must surface `project_versions_enabled`
(default OFF, same is_enabled() call the backend route itself gates
on — routers/project_versions.py) so the frontend's Changes-page link
and route can hide themselves consistently with what the API will
actually allow, same pattern as `ship_via_pr_enabled`/
`automations_page_enabled`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class TestAuthMeProjectVersionsFlag:
    async def test_me_includes_project_versions_enabled_field(self, monkeypatch):
        from routers import auth

        async def _fake_current_dev(_a):
            return {"user_id": "u1", "email": "u1@x.com"}

        async def _fake_is_enabled(flag, user_id=None, tier=None):
            return flag == "project_versions"

        class _FakeUsers:
            async def find_one(self, *a, **k):
                return {"user_id": "u1", "email": "u1@x.com"}

        class _FakeDB:
            dev_users = _FakeUsers()

        monkeypatch.setattr(auth, "current_dev", _fake_current_dev)
        monkeypatch.setattr(auth, "get_db", lambda: _FakeDB())
        monkeypatch.setattr("services.feature_flags.is_enabled", _fake_is_enabled)
        # auth.py mints a fresh token inside `me` — avoid needing a
        # real signing secret for this narrow flag-plumbing test.
        monkeypatch.setattr(auth, "create_token", lambda *a, **k: "tok")

        result = await auth.me(authorization="Bearer x")
        assert result["project_versions_enabled"] is True

    async def test_defaults_off_when_flag_disabled(self, monkeypatch):
        from routers import auth

        async def _fake_current_dev(_a):
            return {"user_id": "u1", "email": "u1@x.com"}

        async def _fake_is_enabled(flag, user_id=None, tier=None):
            return False

        class _FakeUsers:
            async def find_one(self, *a, **k):
                return {"user_id": "u1", "email": "u1@x.com"}

        class _FakeDB:
            dev_users = _FakeUsers()

        monkeypatch.setattr(auth, "current_dev", _fake_current_dev)
        monkeypatch.setattr(auth, "get_db", lambda: _FakeDB())
        monkeypatch.setattr("services.feature_flags.is_enabled", _fake_is_enabled)
        monkeypatch.setattr(auth, "create_token", lambda *a, **k: "tok")

        result = await auth.me(authorization="Bearer x")
        assert result["project_versions_enabled"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
