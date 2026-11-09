"""tests/test_regression_iter2026_55_part2c_c8_ssrf_preview_url_save.py

Part 2c C8 — the SSRF guard must also apply when `preview_url` is
SAVED (routers/cto_projects/management.py::add_project and
::update_project), not only at capture time. Defense in depth: this
is the FIRST of three layers (the other two are inside
services/preview_capture.py::capture_screenshot itself).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.inserted = []
        self.updated = []

    async def find_one(self, query, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items() if not isinstance(v, dict)):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        self.updated.append((query, update))

        class _R:
            matched_count = 1
        return _R()


class TestUpdateProjectRejectsUnsafePreviewUrl:
    @pytest.mark.parametrize("dangerous_url", [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
    ])
    async def test_dangerous_preview_url_is_rejected_with_400(self, dangerous_url):
        from routers.cto_projects.management import UpdateProject, update_project

        fake_db = _FakeColl()
        with (
            patch("routers.cto_projects.management._pkg.current_dev",
                  AsyncMock(return_value={"user_id": "u1"})),
            patch("routers.cto_projects.management._pkg.require_db",
                  MagicMock(return_value=MagicMock(cto_projects=fake_db))),
            pytest.raises(HTTPException) as exc,
        ):
            await update_project(
                "p1", UpdateProject(preview_url=dangerous_url),
                authorization="Bearer x",
            )
        assert exc.value.status_code == 400
        assert fake_db.updated == [], (
            "C8 FIX REQUIRED: an unsafe preview_url must never reach "
            "db.cto_projects.update_one at all."
        )

    async def test_safe_preview_url_is_accepted(self):
        from routers.cto_projects.management import UpdateProject, update_project

        fake_db = _FakeColl(docs=[{"project_id": "p1", "user_id": "u1"}])
        with patch("routers.cto_projects.management._pkg.current_dev",
                   AsyncMock(return_value={"user_id": "u1"})), \
             patch("routers.cto_projects.management._pkg.require_db",
                   MagicMock(return_value=MagicMock(cto_projects=fake_db))), \
             patch("socket.getaddrinfo",
                   MagicMock(return_value=[(2, None, None, "", ("93.184.216.34", 0))])):
            result = await update_project(
                "p1", UpdateProject(preview_url="https://example.com/"),
                authorization="Bearer x",
            )
        assert result.get("ok") is True
        assert len(fake_db.updated) == 1


class TestAddProjectRejectsUnsafePreviewUrl:
    async def test_dangerous_preview_url_is_rejected_before_any_github_call(self):
        from routers.cto_projects.management import AddProject, add_project

        fake_db = _FakeColl()
        github_verify_called = {"hit": False}

        async def _should_never_be_called(**kw):
            github_verify_called["hit"] = True
            return True, None, None

        with (
            patch("routers.cto_projects.management._pkg.current_dev",
                  AsyncMock(return_value={"user_id": "u1"})),
            patch("routers.cto_projects.management._pkg.require_db",
                  MagicMock(return_value=MagicMock(cto_projects=fake_db))),
            patch("services.github_app.verify_installation_for_repo", _should_never_be_called),
            patch("services.signup_guards.emit_funnel_event", AsyncMock(return_value=None)),
            pytest.raises(HTTPException) as exc,
        ):
            await add_project(
                AddProject(
                    name="t", github_url="https://github.com/o/r",
                    installation_id=123,
                    preview_url="http://169.254.169.254/latest/meta-data/",
                ),
                authorization="Bearer x",
            )
        assert exc.value.status_code == 400
        assert github_verify_called["hit"] is False, (
            "C8 FIX REQUIRED: the preview_url check must happen BEFORE "
            "any GitHub verification network call, not after."
        )
        assert fake_db.inserted == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
