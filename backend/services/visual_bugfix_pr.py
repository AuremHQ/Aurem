"""
services/visual_bugfix_pr.py — Phase 4 "Approve & Create PR" action.

Reuses the EXISTING GitHub write primitives this codebase already
ships for Loop Mode's ship-via-PR flow — no new git/PR logic built
from scratch:
  - services.loop_safety.create_or_reuse_branch / open_draft_pr
  - services.github_api_writer.commit_files
  - services.git_identity.resolve_git_identity

Draft PR by default (founder-approved 2026-09-12) — matches the
existing Loop Mode convention and is the safer default (no CI/deploy
triggers fire on a draft PR until the user marks it ready).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def approve_and_create_pr(*, owner: str, repo: str, base_branch: str,
                                 token: str, patch_files: list[dict],
                                 commit_message: str, summary: str,
                                 bug_description: str, report_id: str,
                                 before_image_url: str | None,
                                 after_image_url: str | None,
                                 db, user_id: str) -> dict:
    """Returns {ok, pr_url, branch, error?}. Never raises."""
    from services.loop_safety import (
        aurem_branch_name, create_or_reuse_branch, open_draft_pr,
    )
    from services.github_api_writer import commit_files
    from services.git_identity import resolve_git_identity

    branch = aurem_branch_name("visual-bugfix", report_id[:8])

    ok, err = await create_or_reuse_branch(
        owner=owner, repo=repo, base_branch=base_branch,
        new_branch=branch, token=token,
    )
    if not ok:
        return {"ok": False, "error": f"Could not create branch: {err}"}

    try:
        author_name, author_email = await resolve_git_identity(db, user_id)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix_pr] identity resolution failed: %r", e)
        return {"ok": False, "error": "Could not resolve your Git author identity."}

    files = {f["path"]: f["new_content"] for f in patch_files}
    try:
        await commit_files(
            owner=owner, repo=repo, branch=branch, token=token,
            files=files, commit_message=commit_message or "fix: visual bug fix via AUREM",
            author_email=author_email, author_name=author_name,
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix_pr] commit_files failed: %r", e)
        return {"ok": False, "error": f"Could not commit the fix: {type(e).__name__}: {e}"}

    body_lines = [
        f"**Reported bug:** {bug_description or '(no description given)'}",
        "",
        f"**What changed:** {summary or 'See diff below.'}",
        "",
        "**Before / After**",
    ]
    if before_image_url:
        after_cell = f"![after]({after_image_url})" if after_image_url else "_preview unavailable_"
        body_lines.append(
            f"| Before | After |\n|---|---|\n| ![before]({before_image_url}) | {after_cell} |"
        )
    body_lines.append("\n_Opened automatically by AUREM's Visual Bug-Fix pipeline._")

    pr_url, err = await open_draft_pr(
        owner=owner, repo=repo, head_branch=branch, base_branch=base_branch,
        title=(commit_message or "Visual bug fix")[:255],
        body="\n".join(body_lines), token=token,
    )
    if not pr_url:
        return {"ok": False, "error": f"Branch pushed but PR creation failed: {err}",
                "branch": branch}

    return {"ok": True, "pr_url": pr_url, "branch": branch}
