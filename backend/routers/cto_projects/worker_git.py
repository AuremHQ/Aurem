"""routers/cto_projects/worker_git.py — git-binary adapter (Fix #15).

Thin adapter kept for the module shape from the 2026-09-08 split and
for the existing test suite, which patches
routers.cto_projects._run_task_with_git. The real pipeline lives in
worker_api._run_task_via_api; this only selects the git commit
mechanism (clone with the git binary, read from disk, git commit /
git push) via commit_mode set to git. Every safety stage runs in the
shared pipeline, identically on both paths.
"""


from typing import Optional

from .worker_api import _run_task_via_api


async def _run_task_with_git(task_id, proj, task, files, context, user_token, maxx_mode: bool = False,
                             resume_edits: Optional[dict] = None):
    """Delegate to the unified pipeline with the git commit mechanism."""
    return await _run_task_via_api(task_id, proj, task, files, context, user_token, maxx_mode,
                                   resume_edits, commit_mode="git")
