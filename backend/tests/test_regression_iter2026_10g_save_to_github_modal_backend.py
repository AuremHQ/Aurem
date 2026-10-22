"""tests/test_regression_iter2026_10g_save_to_github_modal_backend.py

Part B — "Save to GitHub" modal, backend half.

1. POST /cto/tasks/submit now accepts optional repo/branch/
   create_branch/base_branch. A branch name must pass
   services.loop_safety.validate_branch_name; a repo must be in the
   caller's own GitHub-App-installation writable list (never another
   user's repos/token). Omitting both fields entirely keeps today's
   behavior unchanged (no `ship_target` stored on the task doc).
2. GET /cto/projects/{id}/github/repos and .../github/branches are
   new read-only endpoints, both scoped to a project the caller owns
   and a token resolved only from THAT project's installation.
3. services.loop_safety.resolve_ship_target is the single choke point
   both worker_api.py and worker_git.py call — covered directly here
   so the branch-creation-fails-closed and the old-client-fallback
   paths are proven independent of either worker's surrounding
   pipeline (already covered end-to-end by the iter2026_10c/10d files).
"""
from __future__ import annotations

import time
import types
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if row.get(k) != v:
                return False
        return True

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if self._match(r, query)]
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return types.SimpleNamespace(inserted_id="new")

    async def find_one_and_update(self, query, update, projection=None):
        return None

    def find(self, query=None, projection=None, sort=None, limit=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


USER = {"user_id": "u1", "email": "user@example.com", "tier": "pro",
        "is_admin": False, "is_unlimited": False, "track": "dev",
        "created_at": time.time()}

AUTH = {"Authorization": "Bearer u1"}

PROJ = {
    "project_id": "p1", "user_id": "u1",
    "github_owner": "acme", "github_repo": "widgets", "branch": "main",
    "installation_id": "999",
}

REPO_LIST = [
    {"full_name": "acme/widgets", "private": False, "default_branch": "main",
     "permissions": {"push": True, "admin": False}},
    {"full_name": "acme/readonly-repo", "private": False, "default_branch": "main",
     "permissions": {"push": False, "admin": False}},
    {"full_name": "other-org/other-repo", "private": True, "default_branch": "main",
     "permissions": {"push": True, "admin": False}},
]


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from cto_services import db as _dbmod
    from routers import cto_projects as router_mod
    _dbmod.set_db(fake_db)

    async def _fake_current_dev(authorization=None):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return USER

    old_current_dev = router_mod.current_dev
    router_mod.current_dev = _fake_current_dev

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    yield c

    router_mod.current_dev = old_current_dev
    _dbmod.set_db(None)


def _submit_body(**kw):
    base = {"project_id": "p1", "task": "fix the bug in signup.py"}
    base.update(kw)
    return base


class TestSubmitTaskShipTargetValidation:
    def test_omitted_fields_keeps_old_behavior_no_ship_target(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body())
        assert r.status_code == 200, r.text
        assert len(fake_db.cto_tasks.rows) == 1
        assert "ship_target" not in fake_db.cto_tasks.rows[0], (
            "omitted repo/branch must NOT write a ship_target — old clients "
            "must keep today's default behavior unchanged"
        )

    def test_branch_with_bad_name_rejected(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(branch="feature/has a space"))
        assert r.status_code == 400
        assert "space" in r.json()["detail"].lower()
        assert fake_db.cto_tasks.rows == [], "a rejected submit must insert no task row"

    def test_branch_with_path_traversal_rejected(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(branch="a/../b"))
        assert r.status_code == 400

    def test_repo_without_branch_rejected(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(repo="acme/widgets"))
        assert r.status_code == 400
        assert "branch is required" in r.json()["detail"].lower()

    def test_repo_not_in_writable_list_rejected(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(return_value=REPO_LIST)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(repo="someone-else/private-repo",
                                                branch="auremcto/ship-x"))
        assert r.status_code == 403
        assert fake_db.cto_tasks.rows == []

    def test_repo_with_read_only_permission_rejected(self, client, fake_db):
        """acme/readonly-repo IS in the installation's repo list but
        the caller's token has no push permission on it — must be
        rejected the same as a repo not in the list at all."""
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(return_value=REPO_LIST)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(repo="acme/readonly-repo",
                                                branch="auremcto/ship-x"))
        assert r.status_code == 403

    def test_valid_repo_and_branch_stores_ship_target(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(return_value=REPO_LIST)), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(repo="acme/widgets", branch="feature/x",
                                                create_branch=False, base_branch="develop"))
        assert r.status_code == 200, r.text
        assert len(fake_db.cto_tasks.rows) == 1
        st = fake_db.cto_tasks.rows[0]["ship_target"]
        assert st == {"repo": "acme/widgets", "branch": "feature/x",
                      "create_branch": False, "base_branch": "develop"}

    def test_branch_only_no_repo_defaults_to_project_repo_implicitly(self, client, fake_db):
        """Branch-only (no repo override) must NOT require the
        writable-repo-list check at all — there's no cross-repo
        choice being made."""
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(side_effect=AssertionError(
                       "must not be called when repo is not overridden"))):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(branch="auremcto/ship-manual"))
        assert r.status_code == 200, r.text
        assert fake_db.cto_tasks.rows[0]["ship_target"]["repo"] is None


class TestGithubBrowseEndpoints:
    def test_list_repos_filters_to_push_only(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        with patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(return_value=REPO_LIST)):
            r = client.get("/api/aurem-dev/cto/projects/p1/github/repos", headers=AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        names = [x["full_name"] for x in body["repos"]]
        assert "acme/widgets" in names
        assert "other-org/other-repo" in names
        assert "acme/readonly-repo" not in names, "no-push repos must be filtered out"
        assert body["default_owner"] == "acme"
        assert body["default_repo"] == "widgets"

    def test_list_repos_404_for_unowned_project(self, client, fake_db):
        r = client.get("/api/aurem-dev/cto/projects/not-mine/github/repos", headers=AUTH)
        assert r.status_code == 404

    def test_list_branches_rejects_repo_not_writable(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        with patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(return_value=REPO_LIST)):
            r = client.get(
                "/api/aurem-dev/cto/projects/p1/github/branches",
                headers=AUTH, params={"owner": "someone-else", "repo": "private-repo"},
            )
        assert r.status_code == 403

    def test_list_branches_returns_branches_for_allowed_repo(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        with patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(return_value=REPO_LIST)), \
             patch("services.loop_safety.list_repo_branches",
                   AsyncMock(return_value=([{"name": "main", "protected": True},
                                             {"name": "dev", "protected": False}], False))):
            r = client.get(
                "/api/aurem-dev/cto/projects/p1/github/branches",
                headers=AUTH, params={"owner": "acme", "repo": "widgets"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["branches"] == [{"name": "main", "protected": True},
                                     {"name": "dev", "protected": False}]
        assert body["truncated"] is False


class TestResolveShipTarget:
    """Direct coverage of services.loop_safety.resolve_ship_target —
    the single choke point both worker_api.py and worker_git.py call.
    End-to-end proof through each worker's full pipeline already
    lives in test_regression_iter2026_10c/10d_*."""

    @pytest.mark.asyncio
    async def test_no_ship_target_falls_back_to_auto_branch(self, fake_db):
        from services.loop_safety import resolve_ship_target
        fake_db.cto_tasks.rows.append({"task_id": "t1"})
        with patch("services.loop_safety.create_or_reuse_branch",
                   AsyncMock(return_value=(True, None))) as branch_mock:
            owner, repo, branch, err = await resolve_ship_target(
                fake_db, task_id="t1", owner="acme", repo="widgets",
                base_branch="main", token="tok",
            )
        assert err is None
        assert owner == "acme" and repo == "widgets"
        assert branch.startswith("auremcto/ship-")
        assert branch_mock.await_count == 1
        _, kwargs = branch_mock.call_args
        assert kwargs["base_branch"] == "main"

    @pytest.mark.asyncio
    async def test_ship_target_with_create_branch_true(self, fake_db):
        from services.loop_safety import resolve_ship_target
        fake_db.cto_tasks.rows.append({
            "task_id": "t2",
            "ship_target": {"repo": "acme/other-repo", "branch": "feature/x",
                             "create_branch": True, "base_branch": "develop"},
        })
        with patch("services.loop_safety.create_or_reuse_branch",
                   AsyncMock(return_value=(True, None))) as branch_mock:
            owner, repo, branch, err = await resolve_ship_target(
                fake_db, task_id="t2", owner="acme", repo="widgets",
                base_branch="main", token="tok",
            )
        assert err is None
        assert (owner, repo, branch) == ("acme", "other-repo", "feature/x")
        _, kwargs = branch_mock.call_args
        assert kwargs["owner"] == "acme" and kwargs["repo"] == "other-repo"
        assert kwargs["base_branch"] == "develop"
        assert kwargs["new_branch"] == "feature/x"

    @pytest.mark.asyncio
    async def test_ship_target_with_create_branch_false_skips_creation(self, fake_db):
        from services.loop_safety import resolve_ship_target
        fake_db.cto_tasks.rows.append({
            "task_id": "t3",
            "ship_target": {"repo": None, "branch": "already-exists",
                             "create_branch": False, "base_branch": None},
        })
        with patch("services.loop_safety.create_or_reuse_branch",
                   AsyncMock()) as branch_mock:
            owner, repo, branch, err = await resolve_ship_target(
                fake_db, task_id="t3", owner="acme", repo="widgets",
                base_branch="main", token="tok",
            )
        assert err is None
        assert (owner, repo, branch) == ("acme", "widgets", "already-exists")
        assert branch_mock.await_count == 0, (
            "create_branch=False must use the branch as-is, no creation call"
        )

    @pytest.mark.asyncio
    async def test_branch_creation_failure_returns_error_no_fallback(self, fake_db):
        from services.loop_safety import resolve_ship_target
        fake_db.cto_tasks.rows.append({
            "task_id": "t4",
            "ship_target": {"repo": None, "branch": "feature/y",
                             "create_branch": True, "base_branch": None},
        })
        with patch("services.loop_safety.create_or_reuse_branch",
                   AsyncMock(return_value=(False, "base_ref_status_404"))):
            _owner, _repo, _branch, err = await resolve_ship_target(
                fake_db, task_id="t4", owner="acme", repo="widgets",
                base_branch="main", token="tok",
            )
        assert err is not None
        assert "base_ref_status_404" in err
        assert "main" in err  # untouched base branch named in the error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
