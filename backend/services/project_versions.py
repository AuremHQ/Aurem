"""
services/project_versions.py — Part 2c, Project Versions feature.

Permanent, never-renumbered per-project change history. Every change
AUREM lands on a user's repo via a MERGED PR gets exactly one row
here, numbered v1, v2, v3... within that project. Recording is always
on (invisible, cheap, best-effort — never raises into the webhook
handler, never delays its response); the read API and the "Changes"
page are gated by feature flag `project_versions` (default OFF).

Row shape (NO file contents, NO tokens, NO diff — see AGENTS.md §8,
never log/store anything resembling repo content or secrets here):
  version_id, user_id, project_id, n, merged_sha, pr_url, pr_number,
  branch, source ("ship_pr"|"fix_batch"), summary (<=200 chars,
  sanitized), loop_id/batch_id (optional), merged_at, status
  ("merged"|"rolled_back"), created_at.

Number allocation is atomic (`find_one_and_update($inc)` on a counter
field of `cto_projects`) — gaps in `n` are allowed (a failed/aborted
allocation just skips a number); numbers are NEVER reused or
renumbered. The two uniqueness guarantees ((project_id, merged_sha)
dedupes a webhook redelivery; (project_id, n) is the belt-and-
suspenders backstop for the atomic counter) live in
services/db_indexes.py::ensure_dedup_indexes.
"""
from __future__ import annotations

import logging
import re
import time
import uuid

from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger("aurem-dev.project_versions")

_COLLECTION = "project_versions"
_VALID_SOURCES = frozenset({"ship_pr", "fix_batch"})
_VALID_STATUSES = frozenset({"merged", "rolled_back"})
_MAX_SUMMARY_LEN = 200
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


async def ensure_read_index(db) -> None:
    """Plain (non-unique) read index for the listing query — owned
    here (the service that runs the query), not db_indexes.py (which
    owns only the dedup/uniqueness constraints)."""
    if db is None:
        return
    try:
        await db[_COLLECTION].create_index(
            [("user_id", ASCENDING), ("project_id", ASCENDING), ("n", DESCENDING)],
            name="user_project_n_desc", background=True,
        )
    except Exception as e:                                  # noqa: BLE001
        logger.warning("ensure_read_index failed: %r", e)


def _sanitize_summary(summary: str | None) -> str:
    """<=200 chars, control characters stripped — same convention as
    services/finding_scope.py::_sanitize_free_text. The summary is
    free text (a commit/PR title) rendered as plain React text on the
    Changes page — never HTML, so no markup-escaping is needed here,
    only length/control-byte hygiene."""
    if not isinstance(summary, str):
        return ""
    return _CONTROL_CHAR_RE.sub("", summary).strip()[:_MAX_SUMMARY_LEN]


def is_valid_merged_sha(sha: str | None) -> bool:
    return isinstance(sha, str) and bool(_SHA_RE.match(sha))


async def record_version_on_merge(
    db, *, user_id: str, project_id: str, merged_sha: str,
    pr_url: str, pr_number, branch: str, source: str,
    summary: str = "", loop_id: str | None = None,
    batch_id: str | None = None,
) -> None:
    """Best-effort — NEVER raises into the webhook handler, NEVER
    delays its response. Called from services/loop_safety.py::
    dispatch_pull_request_webhook for BOTH the `aura:ship` and
    `aura:fix-batch` doors.

    `user_id`/`project_id` MUST already be resolved from OUR OWN
    stored rows (loop_outcomes for ship, fix_batches for a batch) by
    the caller — never pass anything read directly off the
    (unauthenticated, GitHub-controlled) webhook payload.

    Number allocation is atomic: `find_one_and_update($inc)` on
    `cto_projects.version_counter`. Gaps in `n` are allowed (e.g. this
    function returns early for a malformed input before ever
    allocating); numbers are NEVER reused — a webhook redelivery of
    the SAME merge is caught by the (project_id, merged_sha) unique
    index and silently ignored, never double-counted."""
    try:
        if not (isinstance(user_id, str) and user_id
                and isinstance(project_id, str) and project_id):
            return
        if not is_valid_merged_sha(merged_sha):
            return
        if source not in _VALID_SOURCES:
            return

        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError

        proj = await db.cto_projects.find_one_and_update(
            {"project_id": project_id},
            {"$inc": {"version_counter": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not proj:
            logger.info("record_version_on_merge: project %s not found — skip", project_id)
            return
        n = proj.get("version_counter")

        row = {
            "version_id": f"pv_{uuid.uuid4().hex[:12]}",
            "user_id": user_id, "project_id": project_id, "n": n,
            "merged_sha": merged_sha, "pr_url": pr_url, "pr_number": pr_number,
            "branch": branch, "source": source,
            "summary": _sanitize_summary(summary),
            "loop_id": loop_id, "batch_id": batch_id,
            "merged_at": time.time(), "status": "merged",
            "created_at": time.time(),
        }
        try:
            await db[_COLLECTION].insert_one(row)
        except DuplicateKeyError:
            # Webhook redelivery (same project_id+merged_sha) — or,
            # far rarer, a lost race on `n` despite the atomic
            # counter (e.g. a pre-existing row from before this
            # feature shipped) — either way, silently do nothing
            # further. The allocated `n` is simply never reused.
            logger.info(
                "record_version_on_merge: duplicate (project_id=%s "
                "merged_sha=%s or n=%s) — redelivery, no-op",
                project_id, merged_sha, n,
            )
    except Exception as e:                                   # noqa: BLE001
        logger.warning("record_version_on_merge failed (non-fatal): %r", e)


async def mark_rolled_back(db, *, loop_id: str | None = None,
                           batch_id: str | None = None) -> None:
    """Best-effort — NEVER raises. Called from the ONE existing
    function that reverts a merged ship (services/loop_rollback.py::
    run_rollback, shared by the G12 admin path and the R10 one-click
    user path) and from services/finding_fix_applier.py::revert_batch
    (the fix-batch equivalent) — no new rollback logic, just marking
    the already-recorded version row."""
    if not (loop_id or batch_id):
        return
    try:
        query = {"status": "merged"}
        if loop_id:
            query["loop_id"] = loop_id
        else:
            query["batch_id"] = batch_id
        await db[_COLLECTION].update_many(
            query, {"$set": {"status": "rolled_back"}},
        )
    except Exception as e:                                   # noqa: BLE001
        logger.warning("mark_rolled_back failed (non-fatal): %r", e)
