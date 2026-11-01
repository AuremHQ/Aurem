"""
2026-09-17 (bug #6, round 2) — Founder live-verification on production
found the codebase Graph tab getting MORE stale (7924m -> 9260m) despite
real commits, right after exercising the "fix the critical issues"
auto-fix flow. Root cause: `apply_finding_fix()` in
`services/finding_fix_applier.py` is a THIRD real-commit path, separate
from the one-shot workers (`_run_task_with_git` / `_run_task_via_api`)
that already got the post-commit graph-refresh hook in round 1. This
third path never scheduled a refresh at all.

Verifies:
  * A successful `apply_finding_fix()` call schedules
    `refresh_graph_after_commit` exactly once, using the project's base
    `branch` (NOT the branch-per-fix `commit_target`), since the Graph
    tab always reflects the project's base branch.
  * A failed commit (github_push_failed) must NOT schedule a refresh.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock


@pytest.fixture(autouse=True)
def _reset_batch_locks():
    import services.fix_batches as fb
    fb._batch_locks.clear()
    yield
    fb._batch_locks.clear()


def _base_db():
    class _Proj:
        async def find_one(self, q, proj=None):
            return {
                "project_id": "p1",
                "github_owner": "polarisbuiltinc",
                "github_repo": "auremdev",
                "github_branch": "main",
                "github_token": None,
            }

    class _Fixes:
        async def insert_one(self, doc):
            pass

    # FIX_one_branch_for_all_fixes (2026) — apply_finding_fix now
    # always resolves/creates a shared fix_batches row before
    # touching the repo. Same minimal fake shape as
    # tests/test_regression_iter2026_34_single_fix_branch_apply_fix.
    # py's _FakeFixBatches (dict-backed, no real Mongo needed).
    class _FixBatches:
        def __init__(self):
            self.docs = []

        async def find_one(self, filt, proj=None):
            for d in self.docs:
                if all(d.get(k) == v for k, v in filt.items()):
                    return dict(d)
            return None

        async def insert_one(self, row):
            self.docs.append(dict(row))

        async def find_one_and_update(self, filt, update, return_document=False):
            for d in self.docs:
                if all(d.get(k) == v for k, v in filt.items()):
                    for k, v in update.get("$inc", {}).items():
                        d[k] = d.get(k, 0) + v
                    for k, v in update.get("$push", {}).items():
                        d.setdefault(k, []).append(v)
                    for k, v in update.get("$set", {}).items():
                        d[k] = v
                    return dict(d)
            return None

    class _DB:
        def __init__(self):
            self.cto_projects = _Proj()
            self.finding_fixes = _Fixes()
            self.fix_batches = _FixBatches()

        def __getitem__(self, name):
            return getattr(self, name)

    return _DB()


def _mock_batch_github(monkeypatch, ls):
    """Same batch-creation GitHub surface Phase-1's apply_finding_fix
    now always goes through, mocked the same way test file 34 does."""
    async def _create_branch(**kw):
        return True, None

    async def _open_pr(**kw):
        return {"ok": True, "pr_url": "https://github.com/x/y/pull/1",
                "pr_number": 1, "reused": False, "error": None}

    async def _add_label(**kw):
        return True, None

    async def _update_body(**kw):
        return True, None

    async def _get_status(**kw):
        return {"ok": True, "merged": False, "state": "open", "merge_commit_sha": None}

    monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)
    monkeypatch.setattr(ls, "open_or_reuse_pr", _open_pr)
    monkeypatch.setattr(ls, "add_pr_label", _add_label)
    monkeypatch.setattr(ls, "update_pr_body", _update_body)
    monkeypatch.setattr(ls, "get_pr_status", _get_status)


@pytest.mark.asyncio
async def test_apply_finding_fix_schedules_graph_refresh_on_success(monkeypatch):
    from services import finding_fix_applier as ff

    monkeypatch.setattr("services.pat_vault.get_repo_token_or_error",
                         AsyncMock(return_value=("ghp_realtoken", None, None)))
    monkeypatch.setattr(ff, "_fetch_file_content",
                         AsyncMock(return_value=("secret = '123'\n", None)))
    monkeypatch.setattr(ff, "_generate_patched_content",
                         AsyncMock(return_value=("secret = os.environ['S']\n", None)))
    monkeypatch.setattr(ff, "_finding_still_present", lambda *a, **k: False)

    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files", AsyncMock(return_value={
        "sha": "abc1234", "full_sha": "abc1234" * 6,
        "html_url": "https://github.com/polarisbuiltinc/auremdev/commit/abc1234",
    }))

    import services.loop_safety as ls
    # Simulate the shared batch branch succeeding onto a NEW branch —
    # the refresh must still target `branch` (main), not this branch.
    _mock_batch_github(monkeypatch, ls)

    scheduled = {}
    real_create_task = asyncio.create_task
    def fake_create_task(coro):
        scheduled["coro"] = coro
        return real_create_task(coro)
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    refresh_mock = AsyncMock(return_value=None)
    import services.graph_builder as gb
    monkeypatch.setattr(gb, "refresh_graph_after_commit", refresh_mock)

    finding = {"rule_id": "secret_generic", "file": "app.py", "line": 1,
               "severity": "critical", "title": "hardcoded secret"}
    res = await ff.apply_finding_fix(
        db=_base_db(), user={"user_id": "u1"}, project_id="p1", finding=finding,
    )
    await asyncio.sleep(0)  # let the scheduled task run

    assert res["ok"] is True
    assert "coro" in scheduled, "apply_finding_fix must schedule a graph refresh task"
    refresh_mock.assert_awaited_once()
    kwargs = refresh_mock.await_args.kwargs
    assert kwargs["branch"] == "main", (
        "must refresh against the project's base branch, not the "
        "branch-per-fix commit_target"
    )
    assert kwargs["gh_owner"] == "polarisbuiltinc"
    assert kwargs["gh_repo"] == "auremdev"


@pytest.mark.asyncio
async def test_apply_finding_fix_no_refresh_on_commit_failure(monkeypatch):
    from services import finding_fix_applier as ff

    monkeypatch.setattr("services.pat_vault.get_repo_token_or_error",
                         AsyncMock(return_value=("ghp_realtoken", None, None)))
    monkeypatch.setattr(ff, "_fetch_file_content",
                         AsyncMock(return_value=("secret = '123'\n", None)))
    monkeypatch.setattr(ff, "_generate_patched_content",
                         AsyncMock(return_value=("secret = os.environ['S']\n", None)))
    monkeypatch.setattr(ff, "_finding_still_present", lambda *a, **k: False)

    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files",
                         AsyncMock(side_effect=RuntimeError("push failed")))
    import services.loop_safety as ls
    _mock_batch_github(monkeypatch, ls)

    refresh_mock = AsyncMock(return_value=None)
    import services.graph_builder as gb
    monkeypatch.setattr(gb, "refresh_graph_after_commit", refresh_mock)

    finding = {"rule_id": "secret_generic", "file": "app.py", "line": 1,
               "severity": "critical", "title": "hardcoded secret"}
    res = await ff.apply_finding_fix(
        db=_base_db(), user={"user_id": "u1"}, project_id="p1", finding=finding,
    )
    await asyncio.sleep(0)

    assert res["ok"] is False
    refresh_mock.assert_not_awaited()
