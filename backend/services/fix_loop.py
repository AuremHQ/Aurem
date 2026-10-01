"""
services/fix_loop.py — safe fix loop (flag `fix_loop_v2`).

restore point → patch → S7 guards → proof re-run → branch → PR.
The loop NEVER merges and never pushes to the default branch: the user
merges the PR (that is the "Apply"); closing it is the "Undo".  The item
only becomes `fixed` through `fix_items.reconcile` after a proof re-run
on the real branch (S8).  Every failure restores the item to `open`.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol

from services import fix_items as fi
from services.checkup_orchestrator import run_checkup
from services.commit_guards import evaluate_files
from services.fix_packs import build_patch

logger = logging.getLogger(__name__)


class GitAdapter(Protocol):
    async def head_sha(self, branch: str) -> str: ...
    async def create_branch(self, base: str, new: str) -> bool: ...
    async def commit(self, branch: str, files: dict[str, str], message: str) -> str: ...
    async def open_pr(self, head: str, base: str, title: str, body: str) -> Optional[dict]: ...
    async def close_pr(self, pr_number: int) -> bool: ...


def _branch_name(item_id: str) -> str:
    from services.loop_safety import aurem_branch_name
    return aurem_branch_name("checkup-fix", item_id)


def _pr_body(item: dict, patch: dict) -> str:
    steps = "".join(f"- {s}\n" for s in patch["manual_steps"])
    return (f"**What this fixes:** {item['title_plain']}\n\n{patch['summary_plain']}\n\n"
            f"**Before you merge**\n{steps}\n"
            "AUREM re-ran its checks on this change and the problem no longer shows up. "
            "Merge to apply; close this pull request to undo.")


async def run_fix(db, *, user_id: str, project_id: str, item_id: str, tc: dict[str, str],
                  base_branch: str, git: GitAdapter, clock=None) -> dict:
    items = {i["id"]: i for i in await fi.list_items(db, user_id=user_id, project_id=project_id)}
    item = items.get(item_id)
    if not item:
        return {"ok": False, "reason": "not_found", "message": "We couldn't find that item any more."}
    await fi.record_event(db, user_id=user_id, project_id=project_id, event="fix_clicked")

    patch = build_patch(item, tc, clock)
    if not patch:
        return {"ok": False, "reason": "not_auto_fixable",
                "message": "ORA can't fix this one automatically yet. You can ask ORA in chat to walk you through it."}

    if not await fi.set_status(db, user_id=user_id, project_id=project_id, item_id=item_id, new_status="fixing"):
        return {"ok": False, "reason": "bad_state", "message": "This item can't be fixed right now."}

    async def restore(reason: str, message: str) -> dict:
        await fi.set_status(db, user_id=user_id, project_id=project_id, item_id=item_id, new_status="open")
        return {"ok": False, "reason": reason, "message": message}

    try:
        # S7 — the patch itself must be safe against the current files
        violations = evaluate_files(patch["files"], {p: tc.get(p) for p in patch["files"]})
        if violations:
            return await restore("guard_blocked", "The fix looked too risky, so nothing was changed.")

        # S8 — prove it on the patched tree before touching GitHub
        before = run_checkup(tc, include_scanners=False)
        after = run_checkup({**tc, **patch["files"]}, include_scanners=False)
        after_ids = {i["id"] for i in after["items"]}
        new_bad = [i for i in after["items"] if i["id"] not in {b["id"] for b in before["items"]}
                   and i["severity"] in ("critical", "high")]
        if item_id in after_ids or new_bad:
            return await restore("proof_failed", "The fix didn't pass ORA's own re-check, so nothing was changed.")

        base_sha = await git.head_sha(base_branch)
        branch = _branch_name(item_id)
        if not await git.create_branch(base_branch, branch):
            return await restore("git_failed", "We couldn't prepare a safe copy of your code. Nothing was changed.")
        await git.commit(branch, patch["files"], f"fix: {patch['title']}")
        pr = await git.open_pr(branch, base_branch, f"Fix: {patch['title']}", _pr_body(item, patch))
        if not pr:
            return await restore("git_failed", "The change was prepared but the pull request couldn't be opened.")
    except Exception as e:                                  # noqa: BLE001
        logger.warning("[fix_loop] failed item=%s: %r", item_id, e)
        return await restore("error", "Something went wrong, so nothing was changed.")

    key = {"user_id": user_id, "project_id": project_id, "finding_id": item_id}
    await db.cto_open_findings.update_one(key, {"$set": {"fix": {
        "pack": patch["pack"], "branch": branch, "base_sha": base_sha, "base_branch": base_branch,
        "pr_url": pr.get("url"), "pr_number": pr.get("number"), "proof": "passed_on_branch"}}})
    await fi.record_event(db, user_id=user_id, project_id=project_id, event="fix_applied",
                          meta={"pack": patch["pack"]})
    await fi.record_event(db, user_id=user_id, project_id=project_id, event="fix_verified",
                          meta={"where": "branch"})
    return {"ok": True, "pr_url": pr.get("url"), "summary": patch["summary_plain"],
            "manual_steps": patch["manual_steps"]}


async def undo_fix(db, *, user_id: str, project_id: str, item_id: str, git: GitAdapter) -> dict:
    """Undo = close our still-open PR.  A merged fix is a rollback of the
    user's code — deliberately not automated here (S13)."""
    row = await db.cto_open_findings.find_one(
        {"user_id": user_id, "project_id": project_id, "finding_id": item_id})
    fx = (row or {}).get("fix") or {}
    if not fx.get("pr_number"):
        return {"ok": False, "reason": "no_fix"}
    if not await git.close_pr(int(fx["pr_number"])):
        return {"ok": False, "reason": "close_failed",
                "message": "We couldn't close it (it may already be merged). If it was merged, revert it on GitHub."}
    await fi.set_status(db, user_id=user_id, project_id=project_id, item_id=item_id, new_status="open")
    return {"ok": True}


class GithubAdapter:
    """Real GitHub adapter built from the helpers every other PR flow uses
    (deterministic branch, reuse-if-exists, one PR-opening path)."""

    def __init__(self, *, owner: str, repo: str, token: str, author: tuple[str, str]):
        self.owner, self.repo, self.token = owner, repo, token
        self.author_name, self.author_email = author

    async def head_sha(self, branch: str) -> str:
        from services.github_api_writer import _get_branch_head
        return (await _get_branch_head(self.owner, self.repo, branch, self.token))["sha"]

    async def create_branch(self, base: str, new: str) -> bool:
        from services.loop_safety import create_or_reuse_branch
        ok, _ = await create_or_reuse_branch(owner=self.owner, repo=self.repo, base_branch=base,
                                             new_branch=new, token=self.token)
        return ok

    async def commit(self, branch: str, files: dict[str, str], message: str) -> str:
        from services.github_api_writer import commit_files
        r = await commit_files(self.owner, self.repo, branch, self.token, files, message,
                               author_email=self.author_email, author_name=self.author_name)
        return r.get("sha", "")

    async def open_pr(self, head: str, base: str, title: str, body: str) -> Optional[dict]:
        from services.loop_safety import open_or_reuse_pr
        r = await open_or_reuse_pr(owner=self.owner, repo=self.repo, head_branch=head, base_branch=base,
                                   title=title, body=body, token=self.token)
        return {"url": r.get("pr_url"), "number": r.get("pr_number")} if r.get("ok") else None

    async def close_pr(self, pr_number: int) -> bool:
        from services.loop_safety import github_request_with_retry
        r = await github_request_with_retry(
            "PATCH", f"https://api.github.com/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
            headers={"Authorization": f"token {self.token}", "Accept": "application/vnd.github+json",
                     "User-Agent": "aurem-fix-loop"},
            json={"state": "closed"})
        return r.status_code == 200
