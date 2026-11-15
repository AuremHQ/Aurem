"""tests/test_regression_iter2026_45_part2b_r5_fail_closed_ownership.py

Part 2b R5 — several ownership checks used the pattern
`if proj.get("user_id") and proj.get("user_id") != user_id: deny`.
Python's `and` short-circuits to False when the left operand is
falsy, so a project/job/batch document with a MISSING or empty
`user_id` (corrupt row, legacy data, a bug elsewhere that failed to
stamp ownership) silently passed the check and was fail-OPEN —
granted to whichever caller asked, instead of denied to everyone.

Fix: every such check is now `if x.get("user_id") != user_id: deny`
— no `and` guard. A document with no verifiable owner is denied to
EVERYONE, never implicitly granted.

Covers services/finding_scope.py::resolve_findings_scope,
routers/findings.py::_assert_owns_project, and
routers/fix_pipeline.py's job-ownership checks on the SSE stream and
polling summary endpoints (admin bypass preserved throughout).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs if docs is not None else []

    async def find_one(self, filt, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                return dict(d)
        return None


class _FakeDB:
    def __init__(self, projects=None):
        self.cto_projects = _FakeColl(projects or [])
        self.team_members = _FakeColl()

    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll

    def __getitem__(self, name):
        return getattr(self, name)


class TestFindingScopeFailClosed:
    async def test_project_with_missing_user_id_is_denied_not_granted(self, monkeypatch):
        """A corrupt/legacy project doc with NO user_id at all must
        be denied to EVERYONE — never silently treated as unowned/
        anyone's-to-read."""
        import services.finding_scope as fs

        async def _fake_check_permission(user, resource, level, db=None):
            return {"effective_account_id": "u1", "permissions": {resource: "owner"}}
        monkeypatch.setattr(fs, "check_permission", _fake_check_permission)

        db = _FakeDB(projects=[{"project_id": "p1"}])  # no user_id key at all
        with pytest.raises(HTTPException) as exc:
            await fs.resolve_findings_scope(db, caller_user_id="u1", project_id="p1")
        assert exc.value.status_code == 403

    async def test_project_owned_by_someone_else_is_denied(self, monkeypatch):
        import services.finding_scope as fs

        async def _fake_check_permission(user, resource, level, db=None):
            return {"effective_account_id": "u1", "permissions": {resource: "owner"}}
        monkeypatch.setattr(fs, "check_permission", _fake_check_permission)

        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "someone_else"}])
        with pytest.raises(HTTPException) as exc:
            await fs.resolve_findings_scope(db, caller_user_id="u1", project_id="p1")
        assert exc.value.status_code == 403

    async def test_project_owned_by_the_caller_is_allowed(self, monkeypatch):
        import services.finding_scope as fs

        async def _fake_check_permission(user, resource, level, db=None):
            return {"effective_account_id": "u1", "permissions": {resource: "owner"}}
        monkeypatch.setattr(fs, "check_permission", _fake_check_permission)

        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}])
        scope = await fs.resolve_findings_scope(db, caller_user_id="u1", project_id="p1")
        assert scope == {"user_id": "u1", "project_id": "p1"}


class TestFindingsRouterAssertOwnsProjectFailClosed:
    async def test_assert_owns_project_denies_missing_owner(self):
        from routers.findings import _assert_owns_project

        db = _FakeDB(projects=[{"project_id": "p1"}])  # no user_id
        with pytest.raises(HTTPException) as exc:
            await _assert_owns_project(db, "u1", "p1")
        assert exc.value.status_code == 403

    async def test_assert_owns_project_denies_mismatched_owner(self):
        from routers.findings import _assert_owns_project

        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "someone_else"}])
        with pytest.raises(HTTPException) as exc:
            await _assert_owns_project(db, "u1", "p1")
        assert exc.value.status_code == 403


class TestFixPipelineJobOwnershipFailClosed:
    async def test_summary_endpoint_denies_access_to_a_job_with_no_recorded_owner(self, monkeypatch):
        """A job dict with user_id == "" (falsy) must be denied to a
        real, non-admin caller — the old `job.get("user_id") and ...`
        guard would have let this slip through fail-open."""
        import routers.fix_pipeline as fp
        from services import fix_job_manager as fjm

        job_id = "fx_orphan_test_45"
        fjm._JOBS[job_id] = {
            "job_id": job_id, "user_id": "", "project_id": "p1",
            "kind": "bulk", "total": 1, "completed": 0, "failed": 0,
            "started_at": 0.0, "closed_at": None, "results": [],
        }
        try:
            async def _fake_gate(authorization, level, db=None):
                caller = {"user_id": "u1", "is_admin": False}
                return caller, {"user_id": "u1"}
            monkeypatch.setattr(fp, "_gate", _fake_gate)
            monkeypatch.setattr(fp, "get_db", lambda: _FakeDB())

            with pytest.raises(HTTPException) as exc:
                await fp.get_job_summary(job_id, authorization="Bearer x")
            assert exc.value.status_code == 404
        finally:
            fjm._JOBS.pop(job_id, None)

    async def test_summary_endpoint_admin_bypass_still_works(self, monkeypatch):
        """Fail-closed for ownership must NOT break the existing admin
        override — an admin can still read any job's summary."""
        import routers.fix_pipeline as fp
        from services import fix_job_manager as fjm

        job_id = "fx_admin_test_45"
        fjm._JOBS[job_id] = {
            "job_id": job_id, "user_id": "someone_else", "project_id": "p1",
            "kind": "bulk", "total": 1, "completed": 0, "failed": 0,
            "started_at": 0.0, "closed_at": None, "results": [],
        }
        try:
            async def _fake_gate(authorization, level, db=None):
                caller = {"user_id": "admin1", "is_admin": True}
                return caller, {"user_id": "admin1"}
            monkeypatch.setattr(fp, "_gate", _fake_gate)
            monkeypatch.setattr(fp, "get_db", lambda: _FakeDB())

            result = await fp.get_job_summary(job_id, authorization="Bearer x")
            assert result["ok"] is True
            assert result["job_id"] == job_id
        finally:
            fjm._JOBS.pop(job_id, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
