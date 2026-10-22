"""tests/test_regression_iter2026_10d_approve_fix_ship_branch_api_path.py

Phase 1b fix — follow-up to Phase 1 (test_regression_iter2026_10c_...).
The Phase 1 report's own scope note turned out to matter: production
containers don't have the `git` binary (`_GIT_AVAILABLE` in
routers/cto_projects/__init__.py), so `_run_task_with_git`
(worker_git.py, fixed in Phase 1) never actually runs there.
`_run_task_via_api` (worker_api.py, commits via the GitHub Data API —
`gh_api_commit`/`services/github_api_writer.py::commit_files`) is the
REAL live path for "Approve the fix" — confirmed by the live
"Creating commit… Pushing ref… Verifying… on remote" log lines, which
only exist in that writer, not in worker_git.py's `git`-binary flow.

This round applies the SAME fix to worker_api.py, reusing the exact
same `services/loop_safety.py` primitives Phase 1 and loop_engine.py's
ship-via-PR path already use — no new branch/PR logic:
  1. `_run_task_via_api` now creates a dedicated branch off the
     project's base branch (`create_or_reuse_branch` + `ship_branch_
     name(task_id)`) BEFORE committing, and commits there via
     `gh_api_commit(branch=ship_branch, ...)` — never the base branch.
  2. Branch-creation failure -> task FAILS, zero commit attempted, no
     fallback to the base branch.
  3. A best-effort draft PR opens via the EXISTING `open_or_reuse_pr`,
     only after the post-push verify below passes.
  4. `ship_branch`/`base_branch`/`pr_url`/`pr_opened` land on the task
     doc, same shape as Phase 1, so TaskProgressCard shows the truth.
  5. The post-push verify step (`_verify_one` -> `gh_api_fetch_file`)
     already read back content at `commit_full_sha` (an immutable
     commit SHA, not a branch name) even before this round — so once
     the commit lands on `ship_branch` instead of the base branch,
     that verify is *automatically* "on ship_branch's SHA": the SHA
     only ever existed on ship_branch to begin with.
  6. Never reads the `ship_via_pr` flag — same as Phase 1.
"""
from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cto_services import db as _dbmod
from routers import cto_projects as router_mod


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

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
        if upsert:
            new_row = dict(query or {})
            new_row.update(update.get("$set") or {})
            self.rows.append(new_row)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return types.SimpleNamespace(inserted_id="new")

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


PROJ = {
    "project_id": "p_shipapi", "user_id": "u_shipapi",
    "github_owner": "acme", "github_repo": "widgets", "branch": "main",
    "installation_id": "111", "tech_stack": "python",
}

ORIGINAL_README = "# widgets\n\nOriginal content.\n"
EDITED_README = "# widgets\n\nOriginal content.\n\nA test comment.\n"
FULL_SHA = "deadbeef00112233445566778899aabbccddeeff"


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture(autouse=True)
def _set_fake_db(fake_db):
    _dbmod.set_db(fake_db)
    yield
    _dbmod.set_db(None)


async def _seed(fake_db, task_id):
    await fake_db.cto_tasks.insert_one({"task_id": task_id, "status": "queued", "started_at": 0})
    await fake_db.dev_users.insert_one({"user_id": "u_shipapi", "tier": "pro"})
    await fake_db.cto_projects.insert_one(dict(PROJ))


async def _fake_fetch(owner, repo, path, ref, token):
    # Post-push verify fetches at the real commit SHA; every earlier
    # read (base-branch content for codegen context) uses the branch
    # name. Returning the post-edit content only for the real SHA
    # keeps the hallucination/verify gates both honest AND passing.
    if ref == FULL_SHA:
        return EDITED_README
    return ORIGINAL_README


async def _fake_commit(owner, repo, branch, token, files, commit_message,
                        author_name, author_email, progress=None):
    return {"sha": FULL_SHA[:7], "full_sha": FULL_SHA}


RESUME_EDITS = {"edits": {"README.md": EDITED_README}, "summary": "add a test comment"}


class TestCommitLandsOnNewBranchNotMain:
    @pytest.mark.asyncio
    async def test_commit_and_verify_target_ship_branch_not_base_branch(self, fake_db):
        task_id = "shipapi_ok"
        await _seed(fake_db, task_id)

        with patch.object(router_mod, "gh_api_fetch_file", AsyncMock(side_effect=_fake_fetch)), \
             patch.object(router_mod, "gh_api_commit", AsyncMock(side_effect=_fake_commit)) as commit_mock, \
             patch("services.git_identity.resolve_git_identity",
                   AsyncMock(return_value=("Jane Dev", "jane@example.com"))), \
             patch("services.vanguard_verify_agent.verify_patch",
                   AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})), \
             patch("services.loop_safety.create_or_reuse_branch",
                   AsyncMock(return_value=(True, None))) as branch_mock, \
             patch("services.loop_safety.open_or_reuse_pr",
                   AsyncMock(return_value={"ok": True, "pr_url": "https://github.com/acme/widgets/pull/7",
                                            "pr_number": 7, "reused": False, "error": None})) as pr_mock:
            await router_mod._run_task_via_api(
                task_id, PROJ, "add a comment to README.md",
                ["README.md"], "", "ghp_faketoken_shipapi1", resume_edits=RESUME_EDITS,
            )

        # Branch created off the real base branch, with a NEW name.
        assert branch_mock.await_count == 1
        _, bkwargs = branch_mock.call_args
        assert bkwargs["base_branch"] == "main"
        ship_branch = bkwargs["new_branch"]
        assert ship_branch != "main"
        assert ship_branch.startswith("auremcto/ship-")

        # THE proof — the actual commit call targeted ship_branch, not "main".
        assert commit_mock.await_count == 1
        _, ckwargs = commit_mock.call_args
        assert ckwargs["branch"] == ship_branch
        assert ckwargs["branch"] != "main"

        # THE proof for requirement #5 — the post-push verify fetch
        # used `ref=FULL_SHA` (the commit that only ever landed on
        # ship_branch): since _fake_fetch only returns the EDITED
        # content for that exact SHA, a passing verify is only
        # possible if the verify step really read back that SHA.
        task_row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert task_row["status"] == "done", f"unexpected: {task_row}"
        assert task_row["verified"] is True
        assert task_row["ship_branch"] == ship_branch
        assert task_row["base_branch"] == "main"
        assert task_row["pr_opened"] is True
        assert task_row["pr_url"] == "https://github.com/acme/widgets/pull/7"
        assert pr_mock.await_count == 1
        _, prkwargs = pr_mock.call_args
        assert prkwargs["head_branch"] == ship_branch
        assert prkwargs["base_branch"] == "main"


class TestBranchCreationFailureBlocksCommit:
    @pytest.mark.asyncio
    async def test_no_commit_when_branch_creation_fails(self, fake_db):
        task_id = "shipapi_branchfail"
        await _seed(fake_db, task_id)

        with patch.object(router_mod, "gh_api_fetch_file", AsyncMock(side_effect=_fake_fetch)), \
             patch.object(router_mod, "gh_api_commit", AsyncMock(side_effect=_fake_commit)) as commit_mock, \
             patch("services.git_identity.resolve_git_identity",
                   AsyncMock(return_value=("Jane Dev", "jane@example.com"))), \
             patch("services.vanguard_verify_agent.verify_patch",
                   AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})), \
             patch("services.loop_safety.create_or_reuse_branch",
                   AsyncMock(return_value=(False, "create_branch_status_403"))), \
             patch("services.loop_safety.open_or_reuse_pr", AsyncMock()) as pr_mock:
            await router_mod._run_task_via_api(
                task_id, PROJ, "add a comment to README.md",
                ["README.md"], "", "ghp_faketoken_shipapi2", resume_edits=RESUME_EDITS,
            )

        task_row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert task_row["status"] == "failed", f"expected failed, got {task_row}"
        assert "branch" in (task_row.get("error") or "").lower()
        assert "create_branch_status_403" in (task_row.get("error") or "")

        # THE proof — no commit was ever attempted, no fallback to main.
        assert commit_mock.await_count == 0, "commit must never fire when branch creation fails"
        assert pr_mock.await_count == 0
        assert "ship_branch" not in task_row
        assert "commit_sha" not in task_row


class TestBehaviorIndependentOfShipViaPrFlag:
    @pytest.mark.asyncio
    async def test_flag_is_never_consulted_by_this_worker(self, fake_db):
        task_id = "shipapi_flagcheck"
        await _seed(fake_db, task_id)

        with patch.object(router_mod, "gh_api_fetch_file", AsyncMock(side_effect=_fake_fetch)), \
             patch.object(router_mod, "gh_api_commit", AsyncMock(side_effect=_fake_commit)), \
             patch("services.git_identity.resolve_git_identity",
                   AsyncMock(return_value=("Jane Dev", "jane@example.com"))), \
             patch("services.vanguard_verify_agent.verify_patch",
                   AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})), \
             patch("services.loop_safety.create_or_reuse_branch",
                   AsyncMock(return_value=(True, None))), \
             patch("services.loop_safety.open_or_reuse_pr",
                   AsyncMock(return_value={"ok": True, "pr_url": "https://github.com/acme/widgets/pull/8",
                                            "pr_number": 8, "reused": False, "error": None})), \
             patch("services.feature_flags.is_enabled", AsyncMock(return_value=True)) as flag_mock:
            await router_mod._run_task_via_api(
                task_id, PROJ, "add a comment to README.md",
                ["README.md"], "", "ghp_faketoken_shipapi3", resume_edits=RESUME_EDITS,
            )

        assert flag_mock.await_count == 0, (
            "worker_api.py must not consult the ship_via_pr flag — "
            "its branch+PR behavior is unconditional on this surface"
        )
        task_row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert task_row["status"] == "done"
        assert task_row["ship_branch"] != "main"
        assert task_row["pr_opened"] is True

    def test_worker_api_source_has_no_ship_via_pr_reference(self):
        src = Path("/app/backend/routers/cto_projects/worker_api.py").read_text()
        assert "ship_via_pr" not in src


class TestDispatcherBranchPerFixOnBothWorkers:
    """Requirement: regardless of whether the `git` binary is present,
    the worker _run_task actually dispatches to always ends up doing
    branch-per-fix. This proves the DISPATCH is correct; each worker's
    own branch-per-fix behavior is proven by its dedicated test class
    above (this file) and in test_regression_iter2026_10c_... (the
    git-binary worker)."""

    @pytest.mark.asyncio
    async def test_dispatches_to_api_worker_when_git_unavailable(self, monkeypatch):
        monkeypatch.setattr(router_mod, "_GIT_AVAILABLE", False)
        api_spy = AsyncMock(return_value=None)
        git_spy = AsyncMock(return_value=None)
        with patch.object(router_mod, "_run_task_via_api", api_spy), \
             patch.object(router_mod, "_run_task_with_git", git_spy):
            await router_mod._run_task(
                "t_dispatch1", PROJ, "task", [], "", "tok",
            )
        assert api_spy.await_count == 1
        assert git_spy.await_count == 0

    @pytest.mark.asyncio
    async def test_dispatches_to_git_worker_when_git_available(self, monkeypatch):
        monkeypatch.setattr(router_mod, "_GIT_AVAILABLE", True)
        api_spy = AsyncMock(return_value=None)
        git_spy = AsyncMock(return_value=None)
        with patch.object(router_mod, "_run_task_via_api", api_spy), \
             patch.object(router_mod, "_run_task_with_git", git_spy):
            await router_mod._run_task(
                "t_dispatch2", PROJ, "task", [], "", "tok",
            )
        assert git_spy.await_count == 1
        assert api_spy.await_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
