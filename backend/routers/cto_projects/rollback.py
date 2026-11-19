"""
routers/cto_projects/rollback.py — AUREM CTO Projects.
Revert a previously-pushed AUREM commit (`git revert --no-edit`,
never a force-push) via the same git-binary/GitHub-API dual path the
task workers use.

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change). Uses
`_pkg.<name>` for anything patched at the package level by the
existing test suite (`current_dev`, `get_db`, `require_db`,
`gh_api_revert`, `_sh`, `WORKSPACE`, `_GIT_AVAILABLE`,
`_run_rollback_via_api`, `_run_rollback_with_git`) — see preview.py's
module docstring for why.
"""
import asyncio
import logging
import shutil
import time

from fastapi import BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

import routers.cto_projects as _pkg
from . import router

logger = logging.getLogger(__name__)


class RollbackBody(BaseModel):
    # User must echo "ROLLBACK" to confirm intent server-side too —
    # double safety on top of the two-click client confirmation.
    confirm: str


@router.post("/tasks/{task_id}/rollback")
async def rollback_task(
    task_id: str,
    body: RollbackBody,
    bg: BackgroundTasks,
    authorization: str = Header(None),
) -> dict:
    """Revert a previously-pushed AUREM commit on the project's repo.
    Uses `git revert --no-edit <sha>` so the rollback is itself a new
    commit (no force-push, full history preserved). Idempotent: a task
    that's already been rolled back returns 409."""
    me = await _pkg.current_dev(authorization)
    if (body.confirm or "").strip().upper() != "ROLLBACK":
        raise HTTPException(400, "Must confirm with 'ROLLBACK'")

    db = _pkg.require_db()
    t = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]}
    )
    if not t:
        raise HTTPException(404, "Task not found")
    if t.get("status") != "done":
        raise HTTPException(
            400,
            f"Only completed tasks can be rolled back (current: {t.get('status')})",
        )
    if not t.get("commit_sha"):
        raise HTTPException(400, "Task has no commit to revert")
    if t.get("rollback_sha"):
        raise HTTPException(409, "Task already rolled back")
    if t.get("rollback_status") in ("queued", "running"):
        raise HTTPException(409, "Rollback already in progress")
    if t.get("rollback_status") == "failed":
        raise HTTPException(
            409,
            "Previous rollback failed — manual intervention required",
        )

    proj = await db.cto_projects.find_one(
        {"project_id": t["project_id"], "user_id": me["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Parent project not found")

    from services.pat_vault import get_repo_token_or_error
    user_token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if not user_token:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")

    await db.cto_tasks.update_one(
        {"task_id": task_id},
        {"$set": {
            "rollback_status": "queued",
            "rollback_started_at": time.time(),
        }},
    )
    bg.add_task(_pkg._run_rollback, task_id, proj, t["commit_sha"], user_token)
    return {"ok": True, "task_id": task_id, "rollback_status": "queued"}


# ── Rollback worker ──────────────────────────────────────────────────────
async def _rollback_log(task_id: str, step: str, status: str = "info"):
    """Append a step to the task's `rollback_steps` array."""
    db = _pkg.get_db()
    if db is None:
        return
    await db.cto_tasks.update_one(
        {"task_id": task_id},
        {"$push": {"rollback_steps": {"step": step, "status": status, "ts": time.time()}}},
    )


async def _run_rollback(task_id: str, proj: dict, commit_sha: str,
                         user_token: str) -> None:
    """Dispatcher — git-subprocess path locally, GitHub-API path in
    production where git isn't installed."""
    if _pkg._GIT_AVAILABLE:
        return await _pkg._run_rollback_with_git(task_id, proj, commit_sha, user_token)
    return await _pkg._run_rollback_via_api(task_id, proj, commit_sha, user_token)


# Tier 3 - the rollback api/git twin, unified in the Fix #15 shape.
# The safety and status stages below are shared, module-level code
# called by BOTH rollback paths in the same order. Only the revert
# mechanism differs: the GitHub Data API in _run_rollback_via_api,# the git binary (clone / revert / push) in _run_rollback_with_git.
class _RollbackRun:
    def __init__(self, task_id, user_token):
        self.task_id = task_id
        self.user_token = user_token
        self.db = _pkg.get_db()

    def scrub(self, s):
        if not s or not self.user_token:
            return s or "",
        return s.replace(self.user_token, "***PAT***")

    async def set(self, **fields):
        if self.db is not None:
            await self.db.cto_tasks.update_one({"task_id": self.task_id}, {"$set": fields})

    async def prog(self, step, status="info"):
        await _rollback_log(self.task_id, step, status)

    async def mark_done(self, new_sha):
        await self.set(rollback_status="done", rollback_sha=new_sha, rollback_completed_at=time.time())

    async def mark_failed(self, err):
        safe = self.scrub(str(err))
        await _rollback_log(self.task_id, f"❌ {safe}", "error")
        await self.set(rollback_status="failed", rollback_error=safe, rollback_completed_at=time.time())


async def _run_rollback_core(task_id, proj, commit_sha, user_token, label, revert):
    run = _RollbackRun(task_id, user_token)
    try:
        await run.set(rollback_status="running", rollback_worker_started_at=time.time())
        new_sha = await revert(run)
        await run.mark_done(new_sha)
    except Exception as e:
        logger.exception(f"[{label} {task_id}] failed")
        await run.mark_failed(e)


async def _run_rollback_via_api(task_id: str, proj: dict, commit_sha: str,
                                  user_token: str) -> None:
    """Pure-API rollback — uses GitHub Git Data API to push a revert
    commit on top of branch HEAD. No force-push, full history preserved."""
    owner = proj["github_owner"]
    repo = proj["github_repo"]
    branch = proj.get("branch", "main")
    async def _revert(run):
        db = run.db
        # 2026-09-1…21 chars truncated…lay ("queued but not yet scheduled" vs. GH API being slow)
        # without needing raw server log access to diagnose next time.
        # Iter 212m-218 — attribute the revert commit to the real
        # developer instead of the historical "AUREM" bot identity.
        from services.git_identity import resolve_git_identity
        _author_name, _author_email = await resolve_git_identity(
            db, proj.get("user_id") or "",
        )
        result = await _pkg.gh_api_revert(
            owner=owner, repo=repo, branch=branch, token=user_token,
            commit_sha=commit_sha, progress=run.prog,
            author_name=_author_name, author_email=_author_email,
        )
        return result["sha"]

    return await _run_rollback_core(task_id, proj, commit_sha, user_token, "rollback-api", _revert)


async def _run_rollback_with_git(task_id: str, proj: dict, commit_sha: str,
                                   user_token: str) -> None:
    """Clone, `git revert --no-edit <sha>`, push the revert commit."""
    async def _revert(run):
        ws = _pkg.WORKSPACE / f"rb_{task_id}"
        ws.mkdir(parents=True, exist_ok=True)
        repo_path = ws / "repo"
        owner = proj["github_owner"]
        repo = proj["github_repo"]
        branch = proj.get("branch", "main")
        clone_url = (f"https://x-access-token:{user_token}@github.com/{owner}/{repo}.git"
        if user_token else f"https://github.com/{owner}/{repo}.git")
        try:
            await _rollback_log(task_id, f"Cloning {owner}/{repo}@{branch}…")
            # Full history needed (no --depth=1) so the revert can find the sha
            # 2026-09-09 — offloaded to a thread (asyncio.to_thread) so these
            # synchronous `subprocess.run()` git calls (up to 120s) never block
            # the shared event loop. This function runs via FastAPI
            # BackgroundTasks on the SAME event loop as every other request
            # (including the trivial /health probe) — a blocked clone/push here
            # was the confirmed root cause of the nginx "/health upstream timed
            # out" bursts that made K8s mark the pod unhealthy mid-deploy.
            r = await asyncio.to_thread(
                _pkg._sh, ["git", "clone", "--branch", branch, clone_url, str(repo_path)],
                cwd=ws, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"git clone failed: {run.scrub(r.stderr)[:300]}")
            await _rollback_log(task_id, "✅ Cloned", "success")

            await asyncio.to_thread(_pkg._sh, ["git", "config", "user.email", "cto@auremcto.com"], repo_path)
            await asyncio.to_thread(_pkg._sh, ["git", "config", "user.name", "AUREM"], repo_path)

            # Use `git revert` so we never force-push; it produces a new commit
            # that undoes the changes. `-m 1` lets us revert merge commits if
            # the original was a merge.
            revert = await asyncio.to_thread(
                _pkg._sh,
                ["git", "revert", "--no-edit", "-m", "1", commit_sha],
                repo_path, timeout=60,
            )
            if revert.returncode != 0:
                # Plain (non-merge) commits don't accept `-m`; retry without it
                await asyncio.to_thread(_pkg._sh, ["git", "revert", "--abort"], repo_path)
                revert = await asyncio.to_thread(
                    _pkg._sh,
                    ["git", "revert", "--no-edit", commit_sha],
                    repo_path, timeout=60,
                )
            if revert.returncode != 0:
                raise RuntimeError(
                    f"git revert failed (possibly conflicts): {run.scrub(revert.stderr)[:300]}"
                )
            await _rollback_log(task_id, f"✏️ Reverted {commit_sha}", "success")

            push = await asyncio.to_thread(_pkg._sh, ["git", "push", "origin", branch], repo_path, timeout=90)
            if push.returncode != 0:
                raise RuntimeError(f"git push failed: {run.scrub(push.stderr)[:300]}")

            new_sha_r = await asyncio.to_thread(_pkg._sh, ["git", "rev-parse", "--short", "HEAD"], repo_path)
            new_sha = new_sha_r.stdout.strip()
            await _rollback_log(task_id, f"🚀 pushed revert — {new_sha}", "success")
        finally:
            shutil.rmtree(ws, ignore_errors=True)
        return new_sha

    return await _run_rollback_core(task_id, proj, commit_sha, user_token, "rollback", _revert)
