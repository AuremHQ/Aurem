"""
services/simplify_pipeline.py — Ticket F (2026-02): simplify/cleanup-only
mode.

Deliberately the STRICTEST entry point into AUREM's own code-editing
pipeline — a single file, a single purpose ("remove genuinely dead/
unreachable code, duplicate imports, unused local variables — change
NOTHING ELSE"), and ZERO tolerance: unlike the general task worker
(`routers/cto_projects/worker_api.py::_run_hallucination_gate`, which
retries once with the real file re-injected before giving up), this
module runs the SAME underlying gate checks with NO retry — any
violation is an immediate refusal. It does not reuse/duplicate the
gate's detection logic (that would risk the two drifting apart); it
imports and calls the exact same functions AGENTS.md section 5
already names as the hallucination/import-preservation gate:
`_dropped_import_reasons`, `_hallucination_reasons`,
`_dropped_statement_reasons`, `_symbol_scope_creep_reasons`,
`_file_scope_creep_reasons` (all from `services/cto_projects_helpers.py`).

Scope boundary (intentional, matches the ticket's own wording — no
more, no less):
  - Refuses up front on a test/fixture path (reuses
    `is_test_or_fixture` from `services/loop_diff_classifier.py`,
    AGENTS.md section 9's "test files are write-blocked by default").
  - Produces a PREVIEW only (new_content + unified diff) — this
    module never writes to GitHub. Actually applying an approved
    simplify edit is the same "commit via the existing task worker"
    path every other AUREM edit already goes through; wiring that
    push is a separate, larger integration outside this ticket's
    literal scope (parallel to Ticket D intentionally stopping at a
    proof receipt, not a frontend viewer).
"""
from __future__ import annotations

import difflib
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SIMPLIFY_TASK_TEMPLATE = (
    "Simplify ONLY the file `{path}`. Remove genuinely dead/unreachable "
    "code, duplicate imports, and unused local variables. Do NOT change "
    "behavior. Do NOT rename, remove, or alter any existing import, "
    "export, function signature, or class. Do NOT touch any file other "
    "than `{path}`. If nothing is safe to simplify, leave the file "
    "byte-for-byte identical."
)


def build_simplify_task_message(path: str) -> str:
    return _SIMPLIFY_TASK_TEMPLATE.format(path=path)


def check_simplify_edit(*, path: str, new_content: str, original_content: str,
                         task_msg: str) -> list[str]:
    """Runs the EXISTING hallucination/import-preservation/scope-creep
    checks (no new detection logic, no retry) against a single
    proposed simplify edit. Returns a list of human-readable
    violation reasons — empty list means clean."""
    from services.cto_projects_helpers import (
        _hallucination_reasons, _dropped_import_reasons,
        _dropped_statement_reasons, _symbol_scope_creep_reasons,
        _file_scope_creep_reasons,
    )
    blocks = {path: new_content}
    originals = {path: original_content}
    return (
        _hallucination_reasons(blocks, originals)
        + _dropped_import_reasons(blocks, originals)
        + _dropped_statement_reasons(task_msg, blocks, originals)
        + _symbol_scope_creep_reasons(task_msg, blocks, originals)
        + _file_scope_creep_reasons(task_msg, blocks, originals)
    )


async def run_simplify_preview(*, owner: str, repo: str, branch: str,
                                token: str, path: str) -> dict:
    """End-to-end simplify-preview for one file. Never raises — every
    failure mode (test file, fetch failure, LLM failure, gate
    violation) returns an honest `{"ok": False, ...}` dict instead.
    Never pushes anything — caller decides what to do with an
    `ok: True` preview."""
    from services.loop_diff_classifier import is_test_or_fixture
    if is_test_or_fixture(path):
        return {
            "ok": False, "refused": True,
            "reason": f"refusing: `{path}` is a test/fixture file — "
                       "simplify-only mode never touches test files "
                       "(AGENTS.md §9).",
        }

    from services.repo_context import _fetch_file
    original = await _fetch_file(owner, repo, path, branch, token)
    if original is None:
        return {"ok": False, "refused": False,
                "reason": f"could not read `{path}` from {owner}/{repo}@{branch}"}

    task_msg = build_simplify_task_message(path)
    from routers.cto_projects.worker_api import _AI_SYS
    from services.llm import call_llm
    try:
        reply = await call_llm(
            messages=[{"role": "user",
                       "content": f"{task_msg}\n\nCurrent content of `{path}`:\n```\n{original}\n```"}],
            system=_AI_SYS, max_tokens=12000, temperature=0.0,
        )
    except Exception as e:
        logger.warning("[simplify] LLM call failed for %s: %r", path, e)
        return {"ok": False, "refused": False, "reason": f"codegen failed: {e}"}

    from services.llm_file_parser import parse_file_blocks
    edits = parse_file_blocks(reply)
    new_content = edits.get(path) or edits.get(path.lstrip("./"))
    if not new_content:
        return {"ok": True, "refused": False, "unchanged": True,
                "new_content": original, "diff": ""}

    violations = check_simplify_edit(
        path=path, new_content=new_content, original_content=original,
        task_msg=task_msg,
    )
    if violations:
        return {
            "ok": False, "refused": True,
            "reason": "refusing — this edit would change behavior and/or "
                       "drop existing code the task never asked to remove:",
            "violations": violations,
        }

    diff = "\n".join(difflib.unified_diff(
        original.splitlines(), new_content.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
    ))
    return {"ok": True, "refused": False, "unchanged": diff == "",
            "new_content": new_content, "diff": diff}


async def apply_simplify_edit(*, db, user_id: str, owner: str, repo: str,
                               branch: str, token: str, path: str,
                               new_content: str) -> dict:
    """Ticket F-UI (2026-02) — apply an already-previewed simplify
    edit, ONLY after the caller's explicit confirmation click (the
    HTTP layer enforces this is a separate call from the preview).

    Defense in depth: re-fetches the CURRENT file content and re-runs
    the exact same gate used by the preview before committing anything
    — never trusts a client-supplied `new_content` blindly, and never
    replays a stale preview against a file that changed upstream since
    it was generated.

    Never commits to `branch` (the project's base/default branch) —
    always a dedicated `aurem/simplify-<path>` branch + a draft PR,
    reusing the EXACT same branch-per-fix + open_or_reuse_pr machinery
    every other AUREM commit path already goes through
    (services/loop_safety.py, services/github_api_writer.py)."""
    from services.loop_diff_classifier import is_test_or_fixture
    if is_test_or_fixture(path):
        return {"ok": False, "refused": True,
                "reason": f"refusing: `{path}` is a test/fixture file."}

    from services.repo_context import _fetch_file
    current = await _fetch_file(owner, repo, path, branch, token)
    if current is None:
        return {"ok": False, "refused": False,
                "reason": f"could not read `{path}` from {owner}/{repo}@{branch}"}

    task_msg = build_simplify_task_message(path)
    violations = check_simplify_edit(
        path=path, new_content=new_content, original_content=current,
        task_msg=task_msg,
    )
    if violations:
        return {
            "ok": False, "refused": True,
            "reason": "refusing — this edit would change behavior and/or "
                       "drop existing code the task never asked to remove:",
            "violations": violations,
        }
    if new_content == current:
        return {"ok": True, "refused": False, "unchanged": True}

    from services.github_api_writer import commit_files
    from services.loop_safety import aurem_branch_name, create_or_reuse_branch, open_or_reuse_pr
    from services.git_identity import resolve_git_identity, build_commit_message

    simplify_branch = aurem_branch_name("simplify", path)
    ok_branch, branch_err = await create_or_reuse_branch(
        owner=owner, repo=repo, base_branch=branch,
        new_branch=simplify_branch, token=token,
    )
    if not ok_branch:
        return {"ok": False, "refused": False,
                "reason": f"branch_create_failed: {branch_err}"}

    author_name, author_email = await resolve_git_identity(db, user_id)
    commit_message = build_commit_message(
        task_type="refactor", summary=f"simplify {path}",
        body="Dead code / duplicate imports / unused local variables "
             "removed. No behavior change — verified by AUREM's "
             "import-preservation safety gate before being offered "
             "for apply.",
    )
    try:
        res = await commit_files(
            owner=owner, repo=repo, branch=simplify_branch, token=token,
            files={path: new_content}, commit_message=commit_message,
            author_name=author_name, author_email=author_email,
        )
    except Exception as e:
        logger.warning("[simplify-apply] commit_files failed for %s: %r", path, e)
        return {"ok": False, "refused": False, "reason": f"github_push_failed: {e}"}

    pr = await open_or_reuse_pr(
        owner=owner, repo=repo, head_branch=simplify_branch, base_branch=branch,
        title=f"refactor: simplify {path}",
        body=(f"**AUREM simplify-only cleanup** for `{path}`.\n\n"
              "Dead code / duplicate imports / unused local variables "
              "removed. No behavior change — this edit passed AUREM's "
              "import-preservation safety gate before being offered "
              f"for apply.\n\nCommit: `{res.get('sha') or ''}`"),
        token=token,
    )
    return {
        "ok": True, "refused": False,
        "commit_sha": res.get("sha"), "commit_url": res.get("html_url"),
        "branch": simplify_branch,
        "pr_url": pr.get("pr_url"), "pr_reused": bool(pr.get("reused")),
    }
