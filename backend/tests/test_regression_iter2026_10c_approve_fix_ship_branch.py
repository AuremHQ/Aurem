"""tests/test_regression_iter2026_10c_approve_fix_ship_branch.py

Phase 1 fix — founder-reported 2026-10-21 production incident: clicking
"Approve the fix" (Mode D chat -> MessageBubble.jsx's shipViaCTO() ->
POST /cto/tasks/submit -> routers/cto_projects/worker_git.py's
_run_task_with_git) pushed the AI-generated commit STRAIGHT to the
project's base branch (commit b58fa79, TJSNDHU/Aurem's `main`) with no
branch choice, no PR, and no warning — even though the pre-approve UI
copy (PrMiniGuide) promised "We'll open a pull request on your GitHub."

Fix (this round, worker_git.py only — loop_engine.py's separate
ship-via-PR path and finding_fix_applier.py are untouched, out of
scope):
  1. `_run_task_with_git` now ALWAYS creates a dedicated branch off the
     project's base branch via the EXISTING
     `services/loop_safety.py::create_or_reuse_branch` +
     `ship_branch_name()` helpers (same primitives loop_engine.py's
     ship-via-PR path and finding_fix_applier.py already use) and
     pushes the commit there — never to the base branch directly.
  2. If branch creation fails, the task FAILS with a clear error and
     NO push is attempted (`git push`/`git commit` never both land) —
     no silent fallback to the base branch.
  3. A best-effort draft PR is opened via the EXISTING
     `open_or_reuse_pr()` helper — no new PR logic. PR-open failure
     never loses the already-landed commit; `pr_opened`/`pr_url` on
     the task doc reflect exactly what happened.
  4. None of this reads the `ship_via_pr` feature flag — unlike
     loop_engine.py's path, this worker never checked that flag to
     begin with, so behavior (and the PrMiniGuide copy on this
     surface, in ShipDialog.jsx/ShipApproveCard.jsx/PrMiniGuide.jsx)
     must be identical regardless of the flag's value.
"""
from __future__ import annotations

import subprocess
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
    "project_id": "p_shipbr", "user_id": "u_shipbr",
    "github_owner": "acme", "github_repo": "widgets", "branch": "main",
    "installation_id": "111", "tech_stack": "python",
}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture(autouse=True)
def _set_fake_db(fake_db):
    _dbmod.set_db(fake_db)
    yield
    _dbmod.set_db(None)


def _fake_sh_factory(repo_path: Path, sh_calls: list):
    def _fake_sh(cmd, cwd=None, timeout=None, **kwargs):
        sh_calls.append(cmd)
        if cmd[:2] == ["git", "clone"]:
            repo_path.mkdir(parents=True, exist_ok=True)
            (repo_path / "README.md").write_text("# widgets\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if len(cmd) > 1 and cmd[1] == "commit":
            return subprocess.CompletedProcess(cmd, 0, "1 file changed", "")
        if len(cmd) > 1 and cmd[1] == "rev-parse":
            stdout = "sha1234" if "--short" in cmd else "sha1234567890abcdef"
            return subprocess.CompletedProcess(cmd, 0, stdout, "")
        if len(cmd) > 1 and cmd[1] == "push":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return _fake_sh


async def _seed(fake_db, task_id):
    await fake_db.cto_tasks.insert_one({"task_id": task_id, "status": "queued"})
    await fake_db.dev_users.insert_one({"user_id": "u_shipbr", "tier": "pro"})
    await fake_db.cto_projects.insert_one(dict(PROJ))


RESUME_EDITS = {"edits": {"feature.py": "def add(a, b):\n    return a + b\n"},
                "summary": "add helper"}


async def _passthrough_hallucination_gate(task_id, edits, contents, user_msg, ai_sys):
    return edits, None


async def _passthrough_syntax_gate(task_id, edits, user_msg, ai_sys):
    return edits, None


async def _passthrough_lint_gate(task_id, edits):
    return edits, {}, None


class TestCommitLandsOnNewBranchNotMain:
    @pytest.mark.asyncio
    async def test_push_targets_ship_branch_not_base_branch(self, fake_db, tmp_path, monkeypatch):
        monkeypatch.setattr(router_mod, "WORKSPACE", tmp_path)
        task_id = "shipbr_ok"
        repo_path = tmp_path / task_id / "repo"
        await _seed(fake_db, task_id)
        sh_calls: list = []

        with patch.object(router_mod, "_sh", side_effect=_fake_sh_factory(repo_path, sh_calls)), \
             patch("routers.cto_projects.worker_git._run_hallucination_gate", _passthrough_hallucination_gate), \
             patch("routers.cto_projects.worker_git._run_syntax_gate", _passthrough_syntax_gate), \
             patch("routers.cto_projects.worker_git._run_lint_gate", _passthrough_lint_gate), \
             patch("services.vanguard_verify_agent.verify_patch",
                   AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})), \
             patch("services.loop_safety.create_or_reuse_branch",
                   AsyncMock(return_value=(True, None))) as branch_mock, \
             patch("services.loop_safety.open_or_reuse_pr",
                   AsyncMock(return_value={"ok": True, "pr_url": "https://github.com/acme/widgets/pull/1",
                                            "pr_number": 1, "reused": False, "error": None})) as pr_mock:
            await router_mod._run_task_with_git(
                task_id, PROJ, "add a helper function", [], "",
                "ghp_faketoken_shipbr1", resume_edits=RESUME_EDITS,
            )

        # create_or_reuse_branch was called with base_branch=main, and a
        # NEW branch name that is NOT "main".
        assert branch_mock.await_count == 1
        _, kwargs = branch_mock.call_args
        assert kwargs["base_branch"] == "main"
        ship_branch = kwargs["new_branch"]
        assert ship_branch != "main"
        assert ship_branch.startswith("auremcto/ship-")

        # THE proof: the actual `git push` invocation targets the new
        # branch ref, never bare `origin main`.
        push_calls = [c for c in sh_calls if len(c) > 1 and c[1] == "push"]
        assert len(push_calls) == 1, f"expected exactly one push, got {push_calls}"
        assert push_calls[0] == ["git", "push", "origin", f"HEAD:refs/heads/{ship_branch}"]
        assert push_calls[0] != ["git", "push", "origin", "main"]

        task_row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert task_row["status"] == "done", f"unexpected: {task_row}"
        assert task_row["ship_branch"] == ship_branch
        assert task_row["base_branch"] == "main"
        assert task_row["pr_opened"] is True
        assert task_row["pr_url"] == "https://github.com/acme/widgets/pull/1"
        assert pr_mock.await_count == 1


class TestBranchCreationFailureBlocksPush:
    @pytest.mark.asyncio
    async def test_no_push_when_branch_creation_fails(self, fake_db, tmp_path, monkeypatch):
        monkeypatch.setattr(router_mod, "WORKSPACE", tmp_path)
        task_id = "shipbr_branchfail"
        repo_path = tmp_path / task_id / "repo"
        await _seed(fake_db, task_id)
        sh_calls: list = []

        with patch.object(router_mod, "_sh", side_effect=_fake_sh_factory(repo_path, sh_calls)), \
             patch("routers.cto_projects.worker_git._run_hallucination_gate", _passthrough_hallucination_gate), \
             patch("routers.cto_projects.worker_git._run_syntax_gate", _passthrough_syntax_gate), \
             patch("routers.cto_projects.worker_git._run_lint_gate", _passthrough_lint_gate), \
             patch("services.vanguard_verify_agent.verify_patch",
                   AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})), \
             patch("services.loop_safety.create_or_reuse_branch",
                   AsyncMock(return_value=(False, "create_branch_status_403"))), \
             patch("services.loop_safety.open_or_reuse_pr", AsyncMock()) as pr_mock:
            await router_mod._run_task_with_git(
                task_id, PROJ, "add a helper function", [], "",
                "ghp_faketoken_shipbr2", resume_edits=RESUME_EDITS,
            )

        task_row = await fake_db.cto_tasks.find_one({"task_id": task_id})
        assert task_row["status"] == "failed", f"expected failed, got {task_row}"
        assert "branch" in (task_row.get("error") or "").lower()
        assert "create_branch_status_403" in (task_row.get("error") or "")

        # THE proof — no push was ever attempted, and no silent
        # fallback to the base branch happened either.
        push_calls = [c for c in sh_calls if len(c) > 1 and c[1] == "push"]
        assert push_calls == [], f"push must never fire when branch creation fails: {push_calls}"
        assert pr_mock.await_count == 0, "PR must never be attempted if no branch/push landed"
        assert "ship_branch" not in task_row
        assert "commit_sha" not in task_row


class TestBehaviorIndependentOfShipViaPrFlag:
    """`ship_via_pr` (services/feature_flags.is_enabled) gates
    loop_engine.py's SEPARATE ship path. worker_git.py never read it
    before this fix and must not start reading it now — branch+PR
    behavior on this surface must be identical whether that flag is
    ON, OFF, or never queried at all."""

    @pytest.mark.asyncio
    async def test_flag_is_never_consulted_by_this_worker(self, fake_db, tmp_path, monkeypatch):
        monkeypatch.setattr(router_mod, "WORKSPACE", tmp_path)
        await _seed(fake_db, "shipbr_flagcheck")

        async def _run_once(task_id):
            repo_path = tmp_path / task_id / "repo"
            sh_calls: list = []
            with patch.object(router_mod, "_sh", side_effect=_fake_sh_factory(repo_path, sh_calls)), \
                 patch("routers.cto_projects.worker_git._run_hallucination_gate", _passthrough_hallucination_gate), \
                 patch("routers.cto_projects.worker_git._run_syntax_gate", _passthrough_syntax_gate), \
                 patch("routers.cto_projects.worker_git._run_lint_gate", _passthrough_lint_gate), \
                 patch("services.vanguard_verify_agent.verify_patch",
                       AsyncMock(return_value={"pass": True, "summary": "clean", "findings": []})), \
                 patch("services.loop_safety.create_or_reuse_branch",
                       AsyncMock(return_value=(True, None))), \
                 patch("services.loop_safety.open_or_reuse_pr",
                       AsyncMock(return_value={"ok": True, "pr_url": "https://github.com/acme/widgets/pull/9",
                                                "pr_number": 9, "reused": False, "error": None})), \
                 patch("services.feature_flags.is_enabled", AsyncMock(return_value=True)) as flag_mock:
                await router_mod._run_task_with_git(
                    task_id, PROJ, "add a helper function", [], "",
                    "ghp_faketoken_shipbr3", resume_edits=RESUME_EDITS,
                )
            return flag_mock

        flag_mock_on = await _run_once("shipbr_flagcheck")
        # Mocked `is_enabled` to always return True (as if ship_via_pr
        # were ON) — the worker must never have called it either way,
        # because it has no code path that reads the flag at all.
        assert flag_mock_on.await_count == 0, (
            "worker_git.py must not consult the ship_via_pr flag — "
            "its branch+PR behavior is unconditional on this surface"
        )
        task_row = await fake_db.cto_tasks.find_one({"task_id": "shipbr_flagcheck"})
        assert task_row["status"] == "done"
        assert task_row["ship_branch"] != "main"
        assert task_row["pr_opened"] is True

    def test_worker_git_source_has_no_ship_via_pr_reference(self):
        """Source-level guard: the behavior above should not become
        true by accident of a mock not being hit — the string itself
        must not appear in worker_git.py, so a future edit that adds a
        flag-gated branch can't silently recreate the exact
        flag-vs-UI-copy mismatch this round fixed."""
        src = Path("/app/backend/routers/cto_projects/worker_git.py").read_text()
        assert "ship_via_pr" not in src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
