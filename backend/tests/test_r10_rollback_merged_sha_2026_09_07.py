"""
R10 — rollback must revert the LANDED merge commit, verify it landed,
and treat a cancelled PR as "nothing landed" (branch delete only).

  t_ship_sha_captured_on_merge      — pull_request(closed, merged) webhook
                                      persists PR.merge_commit_sha as
                                      loop_outcomes.merged_sha (+ session)
  t_rollback_reverts_merged_sha     — revert-last-ship targets merged_sha,
                                      NOT the pre-merge head, and the revert
                                      commit ends up in main's history
  t_rollback_fail_if_revert_missing — verify_branch_head says "not landed"
                                      → status=failed, user-facing failure
                                      message (no fake "Rolled back")
  t_cancelled_deletes_branch        — pull_request(closed, unmerged) →
                                      branch deleted, outcome nothing_landed,
                                      no revert; revert-last-ship → 409
  t_branch_delete_only_aurem_prefix — delete_ship_branch refuses anything
                                      outside the `auremcto/` namespace
"""
from __future__ import annotations

import pytest

from tests.test_iter367_rollback_fake_success_fix import _MemDB

MERGED = "feedfacefeedfacefeedfacefeedfacefeedface"
PRE    = "abc1234deadbeef1234567890abc1234deadbeef"
BRANCH = "auremcto/ship-l1"


def _pr_payload(*, merged: bool, action="closed"):
    return {
        "action": action,
        "repository": {"full_name": "acme/widgets"},
        "pull_request": {
            "number": 7, "merged": merged, "html_url": "https://github.com/acme/widgets/pull/7",
            "merge_commit_sha": MERGED if merged else None,
            "head": {"ref": BRANCH},
            "labels": [{"name": "aura:ship"}],
        },
    }


def _seed(db, *, pr_status="open"):
    db.loop_outcomes.rows.append({
        "loop_id": "l1", "user_id": "u1", "project_id": "p1",
        "commit_sha": PRE, "ship_branch": BRANCH, "pr_status": pr_status,
        "shipped_at": "2026-09-07T10:00:00+00:00", "reverted": False,
    })
    db.loop_sessions.rows.append({
        "loop_id": "l1", "user_id": "u1", "state": "completed",
        "context": {"commit": {"full_sha": PRE, "pr_branch": BRANCH}},
    })
    db.cto_projects.rows.append({
        "project_id": "p1", "user_id": "u1", "github_owner": "acme",
        "github_repo": "widgets", "branch": "main",
    })


@pytest.mark.asyncio
async def test_t_ship_sha_captured_on_merge():
    from services.loop_safety import dispatch_pull_request_webhook
    db = _MemDB(); _seed(db)
    out = await dispatch_pull_request_webhook(db, payload=_pr_payload(merged=True), action="closed")
    assert out["status"] == "merged" and out["merged_sha"] == MERGED
    row = await db.loop_outcomes.find_one({"loop_id": "l1"})
    assert row["merged_sha"] == MERGED and row["pr_status"] == "merged"
    # (loop_sessions.context.commit.merge_commit_sha is also written — the
    #  in-memory harness can't match dotted filters, covered by real Mongo)


@pytest.mark.asyncio
async def test_t_rollback_reverts_merged_sha(monkeypatch):
    from routers import user_rollback
    from fastapi import BackgroundTasks
    db = _MemDB(); _seed(db, pr_status="merged")
    db.loop_outcomes.rows[0]["merged_sha"] = MERGED

    async def _dev(_a): return {"user_id": "u1", "tier": "pro"}
    async def _tok(project): return "ghs_TOKEN"
    monkeypatch.setattr("routers.user_rollback.current_dev", _dev)
    monkeypatch.setattr("routers.user_rollback.require_db", lambda: db)
    monkeypatch.setattr("services.pat_vault.get_repo_token", _tok)

    # Simulated GitHub: revert creates a commit; branch history = [MERGED, revert]
    main_history = [MERGED]
    async def _revert_commit(**kw):
        assert kw["commit_sha"] == MERGED, "must revert the LANDED commit"
        main_history.append("rev0000" + "0" * 33)
        return {"ok": True, "sha": main_history[-1][:7], "full_sha": main_history[-1],
                "html_url": "https://github.com/acme/widgets/commit/rev0000"}
    async def _verify(owner, repo, branch, expected, token, **kw):
        return {"verified": expected in main_history, "last_sha": main_history[-1]}
    monkeypatch.setattr("services.github_api_writer.revert_commit", _revert_commit)
    monkeypatch.setattr("services.github_api_writer.verify_branch_head", _verify)

    bg = BackgroundTasks()
    res = await user_rollback.revert_last_ship(bg=bg, authorization="x")
    assert res["ok"] is True and res["commit_sha"] == MERGED
    for t in bg.tasks:
        await t()
    sess = await db.loop_sessions.find_one({"loop_id": "l1"})
    assert sess["rollback_status"] == "done", sess.get("rollback_error")
    assert main_history[-1].startswith(sess["rollback_sha"]), "revert commit must be in main's history"


@pytest.mark.asyncio
async def test_t_rollback_fail_if_revert_missing(monkeypatch):
    from services.loop_rollback import run_rollback
    db = _MemDB(); _seed(db, pr_status="merged")
    async def _revert_commit(**kw):
        return {"ok": True, "sha": "rev0000", "full_sha": "rev0000" + "0" * 33, "html_url": "u"}
    async def _verify(owner, repo, branch, expected, token, **kw):
        return {"verified": False, "last_sha": MERGED}   # revert never landed
    monkeypatch.setattr("services.github_api_writer.revert_commit", _revert_commit)
    monkeypatch.setattr("services.github_api_writer.verify_branch_head", _verify)
    project = await db.cto_projects.find_one({"project_id": "p1"})
    await run_rollback(db, "l1", project, MERGED, "ghs_TOKEN")
    sess = await db.loop_sessions.find_one({"loop_id": "l1"})
    assert sess["rollback_status"] == "failed"
    assert "could not be confirmed" in (sess.get("rollback_error") or "").lower()
    assert sess.get("reverted") is not True


@pytest.mark.asyncio
async def test_t_cancelled_deletes_branch(monkeypatch):
    from services.loop_safety import dispatch_pull_request_webhook
    from routers import user_rollback
    from fastapi import BackgroundTasks, HTTPException
    db = _MemDB(); _seed(db)
    deleted = {}
    async def _tok(project): return "ghs_TOKEN"
    async def _delete(*, owner, repo, branch, token):
        deleted.update(owner=owner, repo=repo, branch=branch); return True, None
    monkeypatch.setattr("services.pat_vault.get_repo_token", _tok)
    monkeypatch.setattr("services.loop_safety.delete_ship_branch", _delete)

    out = await dispatch_pull_request_webhook(db, payload=_pr_payload(merged=False), action="closed")
    assert out["status"] == "closed" and out["merged_sha"] is None
    assert deleted == {"owner": "acme", "repo": "widgets", "branch": BRANCH}
    row = await db.loop_outcomes.find_one({"loop_id": "l1"})
    assert row["branch_deleted"] is True and row["nothing_landed"] is True
    assert "merged_sha" not in row

    async def _dev(_a): return {"user_id": "u1", "tier": "pro"}
    monkeypatch.setattr("routers.user_rollback.current_dev", _dev)
    monkeypatch.setattr("routers.user_rollback.require_db", lambda: db)
    with pytest.raises(HTTPException) as ei:
        await user_rollback.revert_last_ship(bg=BackgroundTasks(), authorization="x")
    assert ei.value.status_code == 409 and ei.value.detail["error"] == "pr_not_merged"


@pytest.mark.asyncio
async def test_t_branch_delete_only_aurem_prefix(monkeypatch):
    from services import loop_safety
    called = []
    async def _req(*a, **k):
        called.append(a); raise AssertionError("GitHub must not be called")
    monkeypatch.setattr(loop_safety, "github_request_with_retry", _req)
    for bad in ("main", "aurem/fix-legacy", "auremcto", "feature/x", ""):
        ok, err = await loop_safety.delete_ship_branch(owner="a", repo="r", branch=bad, token="t")
        assert ok is False and err == "GW_BLOCK_non_namespaced_branch", bad
    assert called == []
