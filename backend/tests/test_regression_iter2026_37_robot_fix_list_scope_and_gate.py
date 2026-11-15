"""tests/test_regression_iter2026_37_robot_fix_list_scope_and_gate.py

Robot Fix List — B1 (one shared scope helper) / BE2 (team-account
scope mismatch) / B2 (real-only verification gate + scanner dedup).

Root cause (BE2): `services/local_tools.py::save_finding` wrote
`user_id=bin_ctx.bin_id` (the CALLER's own individual id) while
`routers/findings.py::fix_list` reads `user_id=ctx["effective_account
_id"]` (the shared TEAM-account id, resolved via
`services.team_permissions.check_permission`). For a team MEMBER
these differ, so their own chat-saved findings never showed up. Fixed
by routing `save_finding` through the new
`services/finding_scope.py::resolve_findings_scope()` — the SAME
helper fix_list's grouped endpoint now uses too.

Real local Mongo (MONGO_URL/DB_NAME from tests/conftest.py's .env
loader), same convention as
tests/test_findings_teaser_bridge_2026_08_23.py. GitHub/LLM calls
mocked at the `services.finding_fix_applier._fetch_file_content` /
`services.vanguard_scanner.scan_text` boundary `save_finding` itself
calls.
"""
from __future__ import annotations

import time
import types
from unittest.mock import patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


def _db():
    from cto_services.db import set_db
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")]
    set_db(db)
    return db


async def _seed_project(db, project_id: str, owner_id: str) -> None:
    await db.cto_projects.insert_one({
        "project_id": project_id, "user_id": owner_id,
        "github_owner": "octocat", "github_repo": "Hello-World",
        "github_branch": "main",
    })


def _bin_ctx(*, bin_id: str, pid: str, owner="octocat", repo="Hello-World"):
    return types.SimpleNamespace(
        bin_id=bin_id, pid=pid, repo_owner=owner, repo_name=repo,
        branch="main", pat="ghp_fake", is_founder=False,
    )


async def _cleanup(db, project_id: str, *user_ids: str) -> None:
    await db.cto_open_findings.delete_many({"project_id": project_id})
    await db.cto_projects.delete_many({"project_id": project_id})
    if user_ids:
        await db.team_members.delete_many({"user_id": {"$in": list(user_ids)}})


class TestFindingScopeHelper:
    async def test_rejects_missing_user_or_project(self):
        from services.finding_scope import resolve_findings_scope
        db = _db()
        with pytest.raises(HTTPException) as exc:
            await resolve_findings_scope(db, caller_user_id="", project_id="p1")
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc2:
            await resolve_findings_scope(db, caller_user_id="u1", project_id="")
        assert exc2.value.status_code == 400

    async def test_mongo_operator_injection_in_project_id_rejected(self):
        """A project_id of `{"$ne": None}` must never resolve to ANY
        project via find_one — Motor would otherwise happily treat a
        dict as a query operator if it ever reached the filter
        un-typed. FastAPI's own `str` type-coercion at the route
        layer already blocks this in production; this test locks the
        helper's OWN behavior independent of that outer layer."""
        from services.finding_scope import resolve_findings_scope
        db = _db()
        with pytest.raises(HTTPException) as exc:
            await resolve_findings_scope(
                db, caller_user_id="u1", project_id={"$ne": None},  # type: ignore[arg-type]
            )
        assert exc.value.status_code in (400, 404)

    async def test_idor_foreign_project_gets_403(self):
        from services.finding_scope import resolve_findings_scope
        db = _db()
        owner_id = "pytest_fscope_owner"
        attacker_id = "pytest_fscope_attacker"
        project_id = f"pytest_fscope_proj_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            with pytest.raises(HTTPException) as exc:
                await resolve_findings_scope(
                    db, caller_user_id=attacker_id, project_id=project_id,
                )
            assert exc.value.status_code == 403
        finally:
            await _cleanup(db, project_id)

    async def test_nonexistent_project_gets_404(self):
        from services.finding_scope import resolve_findings_scope
        db = _db()
        with pytest.raises(HTTPException) as exc:
            await resolve_findings_scope(
                db, caller_user_id="u1", project_id="pytest_does_not_exist_x",
            )
        assert exc.value.status_code == 404

    async def test_owner_resolves_to_own_id(self):
        from services.finding_scope import resolve_findings_scope
        db = _db()
        owner_id = "pytest_fscope_owner2"
        project_id = f"pytest_fscope_proj2_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            scope = await resolve_findings_scope(
                db, caller_user_id=owner_id, project_id=project_id,
            )
            assert scope["user_id"] == owner_id
        finally:
            await _cleanup(db, project_id)

    async def test_team_member_resolves_to_owner_account(self):
        """The exact BE2 fix, proven at the helper level: a team
        MEMBER's caller_user_id must resolve to the OWNER's
        effective_account_id, not their own id."""
        from services.finding_scope import resolve_findings_scope
        db = _db()
        owner_id = "pytest_fscope_owner3"
        member_id = "pytest_fscope_member3"
        project_id = f"pytest_fscope_proj3_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        await db.team_members.insert_one({
            "user_id": member_id, "owner_id": owner_id, "status": "active",
            "role_label": "editor", "permissions": {"projects": "read_write"},
        })
        try:
            scope = await resolve_findings_scope(
                db, caller_user_id=member_id, project_id=project_id, level="write",
            )
            assert scope["user_id"] == owner_id
        finally:
            await _cleanup(db, project_id, member_id)

    async def test_verified_only_filter_shape(self):
        from services.finding_scope import verified_only_filter
        assert verified_only_filter() == {"verified": {"$ne": False}}


class TestSaveFindingBE2Scope:
    """BE2 — the WRITE side (save_finding) must land under the SAME
    effective account fix_list reads, for both solo and team-member
    callers."""

    async def test_team_member_chat_finding_visible_under_owner_scope(self, monkeypatch):
        from services import local_tools

        async def _fetch(owner, repo, branch, path, token):
            return "def handler():\n    pass\n", None
        monkeypatch.setattr(
            "services.finding_fix_applier._fetch_file_content", _fetch,
        )

        def _scan(text, filepath, **kw):
            return []  # no scanner hit -> unverified, but still SAVED under the right user
        monkeypatch.setattr("services.vanguard_scanner.scan_text", _scan)

        db = _db()
        owner_id = "pytest_be2_owner"
        member_id = "pytest_be2_member"
        project_id = f"pytest_be2_proj_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        await db.team_members.insert_one({
            "user_id": member_id, "owner_id": owner_id, "status": "active",
            "role_label": "editor", "permissions": {"projects": "read_write"},
        })
        try:
            ctx = {
                "user_id": member_id,
                "bin_ctx": _bin_ctx(bin_id=member_id, pid=project_id),
            }
            res = await local_tools.save_finding(ctx, {
                "title": "Hardcoded API key", "severity": "high",
                "file": "app.py", "line": 1,
            })
            assert res["ok"] is True

            # The row must exist under the OWNER's id — the exact id
            # fix_list queries — not the member's own id.
            row = await db.cto_open_findings.find_one({
                "user_id": owner_id, "project_id": project_id,
                "finding_id": res["finding_id"],
            })
            assert row is not None, "finding written under owner scope"
            under_member = await db.cto_open_findings.find_one({
                "user_id": member_id, "project_id": project_id,
            })
            assert under_member is None, "must NOT also exist under the member's own id"
        finally:
            await _cleanup(db, project_id, member_id)

    async def test_bin_ctx_project_id_never_taken_from_args(self, monkeypatch):
        """BE3 — save_finding has no `project_id` argument at all;
        it is ALWAYS taken from bin_ctx.pid, never from the LLM's
        own tool-call args dict, even if an attacker-controlled LLM
        response tried to smuggle one in."""
        from services import local_tools
        async def _fetch(owner, repo, branch, path, token):
            return "x = 1\n", None
        monkeypatch.setattr(
            "services.finding_fix_applier._fetch_file_content", _fetch,
        )
        monkeypatch.setattr("services.vanguard_scanner.scan_text", lambda *a, **k: [])

        db = _db()
        owner_id = "pytest_be3_owner"
        real_project = f"pytest_be3_real_{int(time.time())}"
        other_project = f"pytest_be3_other_{int(time.time())}"
        await _seed_project(db, real_project, owner_id)
        await _seed_project(db, other_project, owner_id)
        try:
            ctx = {"user_id": owner_id, "bin_ctx": _bin_ctx(bin_id=owner_id, pid=real_project)}
            res = await local_tools.save_finding(ctx, {
                "title": "x", "severity": "low",
                "project_id": other_project,  # attacker-controlled arg, must be ignored
            })
            assert res["ok"] is True
            assert res["file"] == ""
            row = await db.cto_open_findings.find_one({"project_id": other_project})
            assert row is None
        finally:
            await _cleanup(db, real_project)
            await _cleanup(db, other_project)


class TestB2RealOnlyGate:
    async def test_unverifiable_finding_saved_unverified_and_hidden_from_fix_list(self, monkeypatch):
        async def _fetch(owner, repo, branch, path, token):
            return "", "file_not_found"
        monkeypatch.setattr(
            "services.finding_fix_applier._fetch_file_content", _fetch,
        )

        from services import local_tools
        db = _db()
        owner_id = "pytest_b2_owner"
        project_id = f"pytest_b2_proj_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            ctx = {"user_id": owner_id, "bin_ctx": _bin_ctx(bin_id=owner_id, pid=project_id)}
            res = await local_tools.save_finding(ctx, {
                "title": "Fake issue in a file that doesn't exist",
                "severity": "critical", "file": "nope.py", "line": 1,
            })
            assert res["ok"] is True
            assert res["verified"] is False
            row = await db.cto_open_findings.find_one({
                "user_id": owner_id, "project_id": project_id,
            })
            assert row["verified"] is False
            assert row["verified_reason"] == "file_not_found"

            from routers.findings import fix_list
            with patch("routers.findings.current_dev", return_value={"user_id": owner_id}):
                listing = await fix_list(project_id=project_id, limit=20, authorization="Bearer x")
            ids = [it["finding_id"] for it in listing["items"]]
            assert res["finding_id"] not in ids
        finally:
            await _cleanup(db, project_id)

    async def test_line_out_of_range_is_unverified(self, monkeypatch):
        async def _fetch(owner, repo, branch, path, token):
            return "one\ntwo\n", None
        monkeypatch.setattr(
            "services.finding_fix_applier._fetch_file_content", _fetch,
        )
        from services import local_tools
        db = _db()
        owner_id = "pytest_b2_owner2"
        project_id = f"pytest_b2_proj2_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            ctx = {"user_id": owner_id, "bin_ctx": _bin_ctx(bin_id=owner_id, pid=project_id)}
            res = await local_tools.save_finding(ctx, {
                "title": "x", "severity": "high", "file": "a.py", "line": 9999,
            })
            assert res["verified"] is False
        finally:
            await _cleanup(db, project_id)

    async def test_scanner_confirmed_finding_is_verified_and_visible(self, monkeypatch):
        async def _fetch(owner, repo, branch, path, token):
            return "SECRET = 'sk-abc123'\n", None
        monkeypatch.setattr(
            "services.finding_fix_applier._fetch_file_content", _fetch,
        )
        def _scan(text, filepath, **kw):
            return [{"name": "hardcoded_secret", "severity": "critical", "line": 1, "snippet": text}]
        monkeypatch.setattr("services.vanguard_scanner.scan_text", _scan)

        from services import local_tools
        db = _db()
        owner_id = "pytest_b2_owner3"
        project_id = f"pytest_b2_proj3_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            ctx = {"user_id": owner_id, "bin_ctx": _bin_ctx(bin_id=owner_id, pid=project_id)}
            res = await local_tools.save_finding(ctx, {
                "title": "Hardcoded secret key", "severity": "critical",
                "file": "config.py", "line": 1,
            })
            assert res["ok"] is True
            assert res["verified"] is True
            row = await db.cto_open_findings.find_one({"finding_id": res["finding_id"]})
            assert row["verified"] is True
            assert row["rule_id"] == "hardcoded_secret"

            from routers.findings import fix_list
            with patch("routers.findings.current_dev", return_value={"user_id": owner_id}):
                listing = await fix_list(project_id=project_id, limit=20, authorization="Bearer x")
            ids = [it["finding_id"] for it in listing["items"]]
            assert res["finding_id"] in ids
        finally:
            await _cleanup(db, project_id)

    async def test_duplicate_of_existing_scanner_finding_is_not_persisted_twice(self, monkeypatch):
        """BE4 — a chat-audit finding that matches an EXISTING
        scanner-origin row at the same file+line+category collapses
        into that row instead of creating a duplicate."""
        async def _fetch(owner, repo, branch, path, token):
            return "SECRET = 'sk-abc123'\n", None
        monkeypatch.setattr(
            "services.finding_fix_applier._fetch_file_content", _fetch,
        )
        def _scan(text, filepath, **kw):
            return [{"name": "hardcoded_secret", "severity": "critical", "line": 1, "snippet": text}]
        monkeypatch.setattr("services.vanguard_scanner.scan_text", _scan)

        from services import local_tools
        db = _db()
        owner_id = "pytest_b2_owner4"
        project_id = f"pytest_b2_proj4_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            await db.cto_open_findings.insert_one({
                "user_id": owner_id, "project_id": project_id,
                "finding_id": "vanguard::config.py:1:hardcoded_secret",
                "rule_id": "hardcoded_secret", "file": "config.py", "line": 1,
                "status": "open", "severity": "critical", "scanner": "vanguard",
                "verified": True,
            })
            ctx = {"user_id": owner_id, "bin_ctx": _bin_ctx(bin_id=owner_id, pid=project_id)}
            res = await local_tools.save_finding(ctx, {
                "title": "Worded totally differently — secret in config",
                "severity": "critical", "file": "config.py", "line": 1,
            })
            assert res["ok"] is True
            assert res["saved"] is False
            count = await db.cto_open_findings.count_documents(
                {"user_id": owner_id, "project_id": project_id},
            )
            assert count == 1  # no duplicate row created
        finally:
            await _cleanup(db, project_id)


class TestB2BulkInputGate:
    async def test_unverified_finding_dropped_from_bulk_filter_helper(self):
        from services.finding_scope import drop_unverified_findings
        db = _db()
        owner_id = "pytest_b2bulk_owner"
        project_id = f"pytest_b2bulk_proj_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            await db.cto_open_findings.insert_one({
                "user_id": owner_id, "project_id": project_id,
                "finding_id": "fake1", "verified": False, "status": "open",
            })
            findings = [{"id": "fake1", "file": "x.py"}, {"id": "real2", "file": "y.py"}]
            kept = await drop_unverified_findings(
                db, user_id=owner_id, project_id=project_id, findings=findings,
            )
            kept_ids = {f["id"] for f in kept}
            assert kept_ids == {"real2"}
        finally:
            await _cleanup(db, project_id)

    async def test_untracked_findings_pass_through_unchanged(self):
        """A finding with no matching cto_open_findings row at all
        (never tracked) is NOT blocked — we only ever drop something
        explicitly marked verified=False."""
        from services.finding_scope import drop_unverified_findings
        db = _db()
        findings = [{"id": "never-tracked-xyz", "file": "z.py"}]
        kept = await drop_unverified_findings(
            db, user_id="nobody", project_id="nowhere", findings=findings,
        )
        assert kept == findings
