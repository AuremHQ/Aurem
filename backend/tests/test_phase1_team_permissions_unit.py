"""
test_phase1_team_permissions_unit.py — 2026-02 · Phase 1

Fast, no-network unit coverage for the pure logic in
services/team_permissions.py (role presets, level semantics). The
live server-enforcement proof lives in
test_phase1_team_rbac_live_http.py — this file just locks in the
matrix math itself.
"""
from __future__ import annotations

import pytest

from services.team_permissions import (
    RESOURCES, ROLE_PRESETS, preset_matrix, require_permission,
)


def test_every_role_preset_covers_every_resource():
    for role, matrix in ROLE_PRESETS.items():
        assert set(matrix.keys()) == set(RESOURCES.keys()), role


def test_admin_preset_is_full_access_everywhere_including_team_management():
    m = preset_matrix("admin")
    assert all(v == "read_write" for v in m.values())


def test_editor_preset_cannot_touch_team_management():
    m = preset_matrix("editor")
    assert m["team_management"] == "none"
    assert m["projects"] == "read_write"


def test_viewer_preset_is_read_only_and_locked_out_of_team_management():
    m = preset_matrix("viewer")
    assert m["team_management"] == "none"
    assert all(v in ("read", "none") for v in m.values())


def test_unknown_role_label_falls_back_to_viewer_least_privilege():
    m = preset_matrix("not_a_real_role")
    assert m == preset_matrix("viewer")


def test_require_permission_rejects_unknown_resource():
    with pytest.raises(ValueError):
        require_permission("not_a_real_resource", "read")


def test_require_permission_rejects_invalid_level():
    with pytest.raises(ValueError):
        require_permission("projects", "delete")


@pytest.mark.asyncio
async def test_read_write_satisfies_both_read_and_write_required():
    from services import team_permissions as tp

    class _FakeUser(dict):
        pass

    async def _fake_current_dev(_auth):
        return {"user_id": "u1", "email": "x@example.com"}

    class _FakeColl:
        async def find_one(self, *_a, **_k):
            return None  # no team_members row -> caller is the owner

    class _FakeDb:
        team_members = _FakeColl()

    tp.current_dev = _fake_current_dev  # patch module-level import
    import types
    fake_db_module = types.SimpleNamespace(get_db=lambda: _FakeDb())
    import sys
    sys.modules["cto_services.db"] = fake_db_module

    check_read = require_permission("projects", "read")
    check_write = require_permission("projects", "write")
    ctx1 = await check_read("Bearer whatever")
    ctx2 = await check_write("Bearer whatever")
    assert ctx1["permissions"]["projects"] == "read_write"
    assert ctx2["is_owner"] is True
