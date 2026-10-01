"""
services/actions/create_file_action.py — "create this new file" as a
confirmable pending action.

Flow (the same CBR rules as edit/insert in pending_action.py):
  user asks to create <path>  ->  ORA replies with one full draft in a
  code block  ->  this module turns that into a stored pending change
  (only if the draft is real and the path is new)  ->  user says "ok"  ->
  new branch + commit + pull request, never the default branch, and the
  PR link is returned.

Nothing here talks to the model. Branch, commit and PR go through the
helpers every other safe-PR flow uses (loop_safety, fix_loop.GithubAdapter).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from services.reply_integrity import _is_empty_body

logger = logging.getLogger(__name__)

_PATH_RE = re.compile(
    r"(?<![\w./-])((?:[\w.\-]+/)*[\w.\-]+\.(?:md|txt|json|ya?ml|toml|py|jsx?|tsx?|css|html|sh|sql))\b")
_FENCE_RE = re.compile(r"```[^\n`]*\n([\s\S]*?)```")
_CREATE_VERB = re.compile(
    r"\b(?:create|build|make|write|draft|generate|add|set\s*up|banao|bana\s*do)\b", re.I)
MIN_DRAFT_CHARS = 40


def extract_create(user_prompt: str, reply: str) -> Optional[dict]:
    """A path the USER named + a creation request + one real code block
    in the reply. Anything less creates no pending change (CBR-1)."""
    path_m = _PATH_RE.search(user_prompt or "")
    if not path_m or not _CREATE_VERB.search(user_prompt or ""):
        return None
    bodies = [b for b in _FENCE_RE.findall(reply or "") if not _is_empty_body(b)]
    if not bodies:
        return None
    content = max(bodies, key=len).strip("\n")
    if len(content.strip()) < MIN_DRAFT_CHARS:
        return None
    return {"path": path_m.group(1), "content": content + "\n"}


def _safe_path(path: str) -> bool:
    return bool(path) and not path.startswith(("/", "~")) and ".." not in path.split("/") \
        and not path.startswith(".git/")


def _payload_problem(payload: dict, ctx: Optional[dict]) -> Optional[str]:
    path, content = (payload or {}).get("path"), (payload or {}).get("content")
    if not path or not content or not _safe_path(path):
        return "incomplete_or_unsafe_payload"
    if not ctx or not ctx.get("bin_ctx"):
        return "no_repo_context"
    from services.loop_diff_classifier import is_test_or_fixture
    return "test_file_protected" if is_test_or_fixture(path) else None


async def _path_problem(path: str, ctx: dict) -> Optional[str]:
    from services.local_tools import read_repo_file
    res = await read_repo_file({"user_id": ctx.get("user_id"), "project_id": ctx.get("project_id"),
                                "bin_ctx": ctx.get("bin_ctx")}, {"path": path})
    if res and res.get("ok"):
        return "file_already_exists"
    if "not found" not in str((res or {}).get("error") or "").lower():
        return "could_not_confirm_path_is_new"
    return None


async def validate_create(payload: dict, *, ctx: Optional[dict]):
    reason = _payload_problem(payload, ctx) or await _path_problem(payload["path"], ctx)
    if reason:
        return False, None, reason
    from services.commit_guards import evaluate_files
    path, content = payload["path"], payload["content"]
    if evaluate_files({path: content}, {path: None}):
        return False, None, "commit_guard_blocked"
    return True, {"path": path, "content": content,
                  "commit_message": f"docs: add {path} (via chat approval)"}, None


def _slug(path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-").lower()


async def _git_for(db, user_id: str, rc: dict):
    from services.fix_loop import GithubAdapter
    from services.git_identity import resolve_git_identity
    return GithubAdapter(owner=rc["owner"], repo=rc["repo"], token=rc["token"],
                         author=await resolve_git_identity(db, user_id))


async def _push(git, base: str, branch: str, p: dict) -> dict:
    """branch -> commit -> pull request, each step checked and reported."""
    if not await git.create_branch(base, branch):
        return _fail("I couldn't create a branch for this file, so nothing was created.")
    if not await git.commit(branch, {p["path"]: p["content"]},
                            p.get("commit_message") or f"docs: add {p['path']}"):
        return _fail(f"The branch `{branch}` exists but the file was not committed. "
                     "Nothing is in your default branch.")
    pr = await git.open_pr(branch, base, f"Add {p['path']}", _pr_body(p["path"]))
    if not pr or not pr.get("url"):
        return _fail(f"The file is committed on branch `{branch}`, but I couldn't open the pull "
                     "request. Nothing is in your default branch yet.", {"branch": branch})
    return {"ok": True,
            "content": f"Done. I added `{p['path']}` on a new branch and opened a pull request: {pr['url']}\n"
                       "Nothing is in your default branch until you merge it.",
            "verification": {"verified": True, "path": p["path"], "branch": branch},
            "extra": {"pr_url": pr["url"], "pr_number": pr.get("number"), "branch": branch}}


async def execute_create(db, action: dict, *, user: dict, project_id, bin_ctx, git=None) -> dict:
    """Returns {ok, content, verification, extra}. Never raises for an
    expected failure; says plainly what did and did not happen."""
    from services.local_tools import _repo_ctx_from
    from services.loop_safety import aurem_branch_name
    p = action.get("payload") or {}
    rc = _repo_ctx_from({"user_id": user.get("user_id"), "bin_ctx": bin_ctx})
    if not rc or not rc.get("token"):
        return _fail("I can't reach your repo right now, so nothing was created. "
                     "Reconnect the project and ask again.")
    branch = aurem_branch_name("chat-create", _slug(p["path"]))
    if branch == rc["branch"]:
        return _fail("Refusing to write to the default branch. Nothing was created.")
    return await _push(git or await _git_for(db, user.get("user_id"), rc), rc["branch"], branch, p)


def _pr_body(path: str) -> str:
    return (f"Adds `{path}`, drafted in chat and approved by you.\n\n"
            "Review the file, then merge to apply or close this pull request to undo.")


def _fail(message: str, extra: Optional[dict] = None) -> dict:
    return {"ok": False, "content": message, "verification": {"verified": False}, "extra": extra or {}}
