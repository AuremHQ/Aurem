"""
routers/cto_projects/github_browse.py — AUREM CTO Projects.
Part B ("Save to GitHub" modal) — read-only GitHub browsing endpoints
for the repo/branch pickers. Both endpoints are scoped to a project
the CALLER owns, and resolve a token ONLY via that project's own
GitHub App installation (`services.pat_vault.get_repo_token_or_error`)
— never another user's repos or token, never the raw token returned
to the client.
"""
from __future__ import annotations

import logging

from fastapi import Header, HTTPException

import routers.cto_projects as _pkg

from . import router

logger = logging.getLogger(__name__)


async def _project_and_token(project_id: str, user_id: str) -> tuple[dict, str]:
    """Shared ownership + token resolution for both endpoints below.
    Raises HTTPException on any failure — never returns a token-less
    or unowned project."""
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    from services.pat_vault import get_repo_token_or_error
    token, auth_err, auth_detail = await get_repo_token_or_error(proj)
    if not token:
        raise HTTPException(403, f"GitHub App auth failed ({auth_err}): {auth_detail}")
    return proj, token


@router.get("/projects/{project_id}/github/repos")
async def list_writable_repos(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """Repos this project's GitHub App installation can write to —
    the "Repository" dropdown in the Save-to-GitHub modal. Default
    selection (current scope repo) is computed client-side from
    `default_owner`/`default_repo`.
    """
    me = await _pkg.current_dev(authorization)
    proj, _token = await _project_and_token(project_id, me["user_id"])
    installation_id = proj.get("installation_id")
    if not installation_id:
        raise HTTPException(400, "This project has no GitHub App installation on file.")

    from services.github_app import list_installation_repos
    try:
        repos = await list_installation_repos(int(installation_id))
    except Exception as e:                                  # noqa: BLE001
        from services.error_classifier import classify_error
        logger.warning("[github_browse] list_installation_repos failed: %r", e)
        raise HTTPException(502, classify_error(e)["user_message"])

    out = [
        {
            "owner": (r.get("full_name") or "/").split("/", 1)[0],
            "repo":  (r.get("full_name") or "/").split("/", 1)[-1],
            "full_name": r.get("full_name"),
            "private": bool(r.get("private")),
            "default_branch": r.get("default_branch") or "main",
            "can_push": bool((r.get("permissions") or {}).get("push")),
        }
        for r in repos
        if r.get("full_name") and (r.get("permissions") or {}).get("push")
    ]
    out.sort(key=lambda r: r["full_name"].lower())
    return {
        "ok": True,
        "repos": out,
        "default_owner": proj.get("github_owner"),
        "default_repo": proj.get("github_repo"),
    }


@router.get("/projects/{project_id}/github/branches")
async def list_repo_branches_endpoint(
    project_id: str,
    owner: str,
    repo: str,
    authorization: str = Header(None),
) -> dict:
    """Branches for `owner/repo` — the "Existing branch" tab's
    dropdown. `owner/repo` must be in this project's writable-repo
    list (same check /cto/tasks/submit applies before shipping), so
    this never probes a repo the caller's installation can't see.
    """
    me = await _pkg.current_dev(authorization)
    proj, token = await _project_and_token(project_id, me["user_id"])
    installation_id = proj.get("installation_id")
    if not installation_id:
        raise HTTPException(400, "This project has no GitHub App installation on file.")

    from services.github_app import list_installation_repos
    try:
        repos = await list_installation_repos(int(installation_id))
    except Exception as e:                                  # noqa: BLE001
        from services.error_classifier import classify_error
        logger.warning("[github_browse] list_installation_repos failed: %r", e)
        raise HTTPException(502, classify_error(e)["user_message"])

    wanted = f"{owner}/{repo}".lower()
    match = next(
        (r for r in repos if (r.get("full_name") or "").lower() == wanted
         and (r.get("permissions") or {}).get("push")),
        None,
    )
    if not match:
        raise HTTPException(
            403, "That repo isn't in your connected GitHub access (or you don't have push rights to it)."
        )

    from services.loop_safety import list_repo_branches
    try:
        branches, truncated = await list_repo_branches(owner, repo, token)
    except Exception as e:                                  # noqa: BLE001
        from services.error_classifier import classify_error
        logger.warning("[github_browse] list_repo_branches failed: %r", e)
        raise HTTPException(502, classify_error(e)["user_message"])

    return {"ok": True, "owner": owner, "repo": repo,
            "branches": branches, "truncated": truncated}
