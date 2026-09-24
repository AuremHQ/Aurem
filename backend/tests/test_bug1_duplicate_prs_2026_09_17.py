"""
2026-09-17 — Bug 1 fix regression: runaway duplicate bot PRs (389
open on one connected repo, reported live).

Covers the three new pieces beyond `aurem_branch_name` determinism
(tested in test_iter212m115_loop_safety_five_fixes.py):
  1. `finding_fix_applier.apply_finding_fix()` folds `path` into the
     branch identity (two files, same rule, must NOT collide).
  2. Re-running the SAME (rule_id, path) fix reuses an existing open
     PR (comments on it) instead of calling create-PR again.
  3. `services/bot_pr_reaper` groups legacy timestamped duplicate
     branches and closes all but the newest, leaving genuinely
     distinct fixes alone.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock


def _base_db():
    class _Proj:
        async def find_one(self, q, proj=None):
            return {
                "github_owner": "polarisbuiltinc",
                "github_repo": "auremdev",
                "github_branch": "main",
                "github_token": None,
            }

    class _Fixes:
        async def insert_one(self, doc):
            pass

    class _DB:
        cto_projects = _Proj()
        finding_fixes = _Fixes()

    return _DB()


def _patch_happy_path(monkeypatch, ff, commit_mock):
    monkeypatch.setattr("services.pat_vault.get_repo_token_or_error",
                         AsyncMock(return_value=("ghp_realtoken", None, None)))
    monkeypatch.setattr(ff, "_fetch_file_content",
                         AsyncMock(return_value=("secret = '123'\n", None)))
    monkeypatch.setattr(ff, "_generate_patched_content",
                         AsyncMock(return_value=("secret = os.environ['S']\n", None)))
    monkeypatch.setattr(ff, "_finding_still_present", lambda *a, **k: False)
    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files", commit_mock)
    import services.graph_builder as gb
    monkeypatch.setattr(gb, "refresh_graph_after_commit", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_different_files_same_rule_get_different_branches(monkeypatch):
    """Two files tripping the SAME rule must NOT collide onto one
    branch — that would silently let one fix overwrite another's."""
    from services import finding_fix_applier as ff
    import services.loop_safety as ls

    commit_mock = AsyncMock(return_value={
        "sha": "abc1234", "full_sha": "abc1234" * 6,
        "html_url": "https://github.com/x/y/commit/abc1234",
    })
    _patch_happy_path(monkeypatch, ff, commit_mock)
    monkeypatch.setattr(ls, "create_or_reuse_branch",
                         AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(ls, "find_open_pr_for_branch",
                         AsyncMock(return_value=None))
    monkeypatch.setattr(ls, "open_draft_pr",
                         AsyncMock(return_value=("https://github.com/x/y/pull/1", None)))

    res_a = await ff.apply_finding_fix(
        db=_base_db(), user={"user_id": "u1"}, project_id="p1",
        finding={"rule_id": "todo_comment", "file": "a.py", "line": 1,
                 "severity": "low", "title": "todo"},
    )
    res_b = await ff.apply_finding_fix(
        db=_base_db(), user={"user_id": "u1"}, project_id="p1",
        finding={"rule_id": "todo_comment", "file": "b.py", "line": 1,
                 "severity": "low", "title": "todo"},
    )
    branch_a = commit_mock.await_args_list[0].kwargs["branch"]
    branch_b = commit_mock.await_args_list[1].kwargs["branch"]
    assert res_a["ok"] is True and res_b["ok"] is True
    assert branch_a != branch_b, (
        "different files tripping the same rule landed on the SAME "
        "branch — one fix would silently overwrite the other"
    )


@pytest.mark.asyncio
async def test_rerun_same_rule_and_file_reuses_existing_pr_not_duplicate(monkeypatch):
    """Re-running the identical fix must comment on the existing open
    PR, never call create-PR again."""
    from services import finding_fix_applier as ff
    import services.loop_safety as ls

    commit_mock = AsyncMock(return_value={
        "sha": "def5678", "full_sha": "def5678" * 6,
        "html_url": "https://github.com/x/y/commit/def5678",
    })
    _patch_happy_path(monkeypatch, ff, commit_mock)
    monkeypatch.setattr(ls, "create_or_reuse_branch",
                         AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(ls, "find_open_pr_for_branch", AsyncMock(return_value={
        "number": 42, "html_url": "https://github.com/x/y/pull/42",
    }))
    open_pr_mock = AsyncMock(return_value=("https://github.com/x/y/pull/999", None))
    monkeypatch.setattr(ls, "open_draft_pr", open_pr_mock)
    comment_mock = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(ls, "add_pr_comment", comment_mock)

    res = await ff.apply_finding_fix(
        db=_base_db(), user={"user_id": "u1"}, project_id="p1",
        finding={"rule_id": "bare_except", "file": "app.py", "line": 5,
                 "severity": "medium", "title": "bare except"},
    )

    assert res["ok"] is True
    assert res["pr_url"] == "https://github.com/x/y/pull/42", (
        "must reuse the EXISTING PR's url, not fabricate a new one"
    )
    open_pr_mock.assert_not_awaited()
    comment_mock.assert_awaited_once()
    assert comment_mock.await_args.kwargs["pr_number"] == 42


# ─── bot_pr_reaper ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reaper_closes_legacy_duplicates_keeps_newest(monkeypatch):
    from services import bot_pr_reaper as reaper

    open_prs = [
        {"number": 10, "body": "**AUREM auto-fix** for todo_comment",
         "head": {"ref": "aurem/fix-todo_comment-1758100000"}},
        {"number": 11, "body": "**AUREM auto-fix** for todo_comment",
         "head": {"ref": "aurem/fix-todo_comment-1758100777"}},
        {"number": 12, "body": "**AUREM auto-fix** for todo_comment",
         "head": {"ref": "aurem/fix-todo_comment-1758100999"}},
        # A genuinely distinct fix — must be left alone.
        {"number": 13, "body": "**AUREM auto-fix** for bare_except",
         "head": {"ref": "aurem/fix-bare_except-app-py"}},
    ]
    monkeypatch.setattr(reaper, "_list_open_bot_prs",
                         AsyncMock(return_value=open_prs))
    close_calls = []
    async def fake_close(*, owner, repo, pr_number, token):
        close_calls.append(pr_number)
        return True, None
    monkeypatch.setattr(reaper, "close_pr", fake_close)
    comment_calls = []
    async def fake_comment(*, owner, repo, pr_number, body, token):
        comment_calls.append(pr_number)
        return True, None
    monkeypatch.setattr(reaper, "add_pr_comment", fake_comment)

    res = await reaper.reap_duplicate_bot_prs_for_repo(
        owner="o", repo="r", token="t",
    )
    assert res["open_bot_prs"] == 4
    assert sorted(res["closed"]) == [10, 11], (
        "must close the two OLDER duplicates, keep #12 (newest) and "
        "#13 (genuinely distinct fix) untouched"
    )
    assert sorted(close_calls) == [10, 11]
    assert sorted(comment_calls) == [10, 11]


@pytest.mark.asyncio
async def test_reaper_leaves_distinct_fixes_alone(monkeypatch):
    """No duplicates at all — reaper must close nothing."""
    from services import bot_pr_reaper as reaper

    open_prs = [
        {"number": 1, "body": "**AUREM auto-fix** for todo_comment",
         "head": {"ref": "aurem/fix-todo_comment-a-py"}},
        {"number": 2, "body": "**AUREM auto-fix** for bare_except",
         "head": {"ref": "aurem/fix-bare_except-b-py"}},
    ]
    monkeypatch.setattr(reaper, "_list_open_bot_prs",
                         AsyncMock(return_value=open_prs))
    close_mock = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(reaper, "close_pr", close_mock)

    res = await reaper.reap_duplicate_bot_prs_for_repo(
        owner="o", repo="r", token="t",
    )
    assert res["closed"] == []
    close_mock.assert_not_awaited()
