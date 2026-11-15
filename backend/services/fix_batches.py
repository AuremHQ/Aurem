"""
services/fix_batches.py — FIX_one_branch_for_all_fixes.

Root cause (verified against origin/main a6aec75d): services/
finding_fix_applier.py::apply_finding_fix built
`aurem_branch_name("fix", f"{rule_id}-{path}")` — ONE branch + ONE
draft PR per (rule_id, file). A 5-finding scan session opened up to
5 branches and 5 PRs.

This module is the ONE place that owns "which branch/PR does this
project's fixes land on right now" — a single OPEN batch per
(user_id, project_id), persisted in Mongo (`fix_batches`) and backed
by a real GitHub branch + draft PR, reused across every fix until the
batch's PR is merged or closed (by the user, on GitHub) or hits the
50-fix cap.

H6 — branch names are `aurem/fixes-<8 hex>`, deterministic only in
the sense of being generated ONCE and stored; critically NOT derived
from rule_id/file path (no truncation-collision risk) and NEVER
ending in 9+ digits, so `bot_pr_reaper._TRAILING_TS_RE` (`-\\d{9,}$`)
can never fold two distinct batches into one dedup group. Belt-and-
suspenders: the batch PR body never contains any of bot_pr_reaper's
`AUREM_BOT_MARKERS` strings, so a batch PR is never even picked up by
`_list_open_bot_prs` in the first place — see
`test_regression_iter2026_33_...`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

logger = logging.getLogger("aurem-dev.fix_batches")

_COLLECTION = "fix_batches"

# Founder decision (reported, not silently chosen): a batch that
# reaches this many fixes is closed to new fixes and the next fix
# starts a fresh "part 2" batch, so no single PR grows unboundedly
# large (GitHub's PR-body cap is 65,536 chars; a huge single table
# also becomes unreviewable for a human).
MAX_FIXES_PER_BATCH = 50

_TERMINAL_STATUSES = ("merged", "closed")

# Per-(user_id, project_id) in-process lock — H4. A second, cross-
# instance backstop is the unique partial index on fix_batches (see
# services/db_indexes.py::ensure_dedup_indexes); a duplicate-key error
# on insert means another process/worker already won the race, so we
# re-read ITS row instead of erroring.
_batch_locks: dict[tuple[str, str], asyncio.Lock] = {}

# Part 2b R4 — _batch_locks previously grew by one entry per
# (user_id, project_id) pair EVER seen, for the life of the process —
# a slow memory leak on a long-running pod. Bounded with best-effort
# LRU eviction: only a currently-UNLOCKED entry is ever evicted,
# never one an in-flight fix is holding.
_MAX_BATCH_LOCKS = 2000


def _lock_for(user_id: str, project_id: str) -> asyncio.Lock:
    key = (user_id, project_id)
    lock = _batch_locks.get(key)
    if lock is not None:
        # Move to the end (most-recently-used) for LRU eviction order.
        _batch_locks.pop(key)
        _batch_locks[key] = lock
        return lock
    lock = asyncio.Lock()
    _batch_locks[key] = lock
    if len(_batch_locks) > _MAX_BATCH_LOCKS:
        _evict_oldest_unlocked()
    return lock


def _evict_oldest_unlocked() -> None:
    """Best-effort — scans from the oldest (dict insertion order)
    entry forward and removes the first one that isn't currently
    held, so the map shrinks back toward _MAX_BATCH_LOCKS without
    ever pulling a lock out from under an in-flight fix. A pathological
    case where EVERY entry is locked at once simply skips eviction
    this round (bounded further next call, never raises)."""
    for key, lock in list(_batch_locks.items()):
        if not lock.locked():
            del _batch_locks[key]
            return


def generate_batch_branch() -> str:
    """`aurem/fixes-<8 hex>` — NOT derived from rule_id/path (H6).
    8 hex characters can never satisfy bot_pr_reaper's `-\\d{9,}$`
    (needs 9+ DIGIT characters; even an all-numeric-looking 8-hex
    suffix is one character short)."""
    return f"aurem/fixes-{uuid.uuid4().hex[:8]}"


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _escape_markdown(text: str) -> str:
    """Neutralizes Markdown/HTML table-breaking characters AND
    @mentions in untrusted finding text before it goes into a PR body
    table cell. `|` breaks the table itself; backticks around an
    `@name` keep GitHub from resolving it as a real mention/ping
    (security test: "stored XSS/markdown/@mention in titles")."""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    text = re.sub(r"(?<!`)@(\w+)(?!`)", r"`@\1`", text)
    return text[:200]


def build_pr_title(fix_count: int) -> str:
    return f"AUREM fixes — {fix_count} issue{'s' if fix_count != 1 else ''}"


def build_pr_body(rows: list[dict]) -> str:
    """Renders the running fix table. `rows` = [{rule_id, file, line,
    severity, commit_sha, status}, ...]. Capped well under GitHub's
    65,536-char PR body limit — if the table itself would exceed the
    cap, the oldest rows are dropped with a note (never silently
    truncated mid-row, which could leave a dangling `|`)."""
    header = (
        "**AUREM fixes** — this PR carries every automated fix for this "
        "project's current scan session, one commit per fix, on a single "
        "shared branch.\n\n"
        "| Rule | File:Line | Severity | Commit | Status |\n"
        "|---|---|---|---|---|\n"
    )
    footer = "\n\n_Generated by AUREM — review each commit before merging._"
    budget = 65000 - len(header) - len(footer)
    lines: list[str] = []
    used = 0
    omitted = 0
    # Newest first so a cap-driven truncation drops the OLDEST rows,
    # never the most recent fix a user just watched happen.
    for row in reversed(rows):
        rule = _escape_markdown(row.get("rule_id") or "")
        file_line = _escape_markdown(f"{row.get('file') or ''}:{row.get('line') if row.get('line') is not None else '?'}")
        severity = _escape_markdown(row.get("severity") or "")
        commit = (row.get("commit_sha") or "")[:7]
        status = _escape_markdown(row.get("status") or "")
        line = f"| {rule} | {file_line} | {severity} | {commit} | {status} |\n"
        if used + len(line) > budget:
            omitted += 1
            continue
        lines.append(line)
        used += len(line)
    lines.reverse()
    note = f"\n_...and {omitted} earlier fix(es), omitted to stay under the PR body size limit._\n" if omitted else ""
    return header + "".join(lines) + note + footer


async def _verify_batch_open_on_github(batch: dict, *, owner: str, repo: str, token: str) -> str:
    """Returns "open" / "merged" / "closed" / "lookup_failed". A
    lookup failure (network, rate limit) is explicitly distinct from
    a confirmed merged/closed state — the caller must never collapse
    them, or a transient GitHub blip would spuriously mint a second
    batch (H4-adjacent correctness, not just a race)."""
    pr_number = batch.get("pr_number")
    if not pr_number:
        return "lookup_failed"
    from services.loop_safety import get_pr_status
    status = await get_pr_status(owner=owner, repo=repo, pr_number=pr_number, token=token)
    if not status.get("ok"):
        return "lookup_failed"
    if status.get("merged"):
        return "merged"
    if status.get("state") == "closed":
        return "closed"
    return "open"


async def get_or_create_open_batch(
    db, *, user_id: str, project_id: str, owner: str, repo: str,
    base_branch: str, token: str,
) -> dict:
    """Public entry point — acquires the per-(user,project) lock
    itself. Callers that need to hold the SAME lock across the batch
    resolution AND their own follow-up work (e.g.
    finding_fix_applier.apply_finding_fix, which must serialize the
    whole read→patch→commit sequence per batch, not just batch
    lookup — H4) should call `_get_or_create_open_batch_locked`
    directly from inside their own `async with _lock_for(...)` block
    instead — `asyncio.Lock` is NOT reentrant, so nesting this
    function inside an already-held lock of the same key would
    deadlock.

    Returns {"ok": True, "batch": {...}, "created": bool} or
    {"ok": False, "error": str}.

    Cross-user isolation: `user_id`/`project_id` MUST already be the
    authenticated session's own values — this function does not and
    cannot re-derive them; every query filters on both.

    Never silently creates a SECOND open batch for the same (user,
    project): a GitHub lookup failure while verifying an existing
    batch is a retryable error, not a green light to mint a new one.
    """
    if not isinstance(user_id, str) or not user_id:
        return {"ok": False, "error": "invalid_user_id"}
    if not isinstance(project_id, str) or not project_id:
        return {"ok": False, "error": "invalid_project_id"}
    async with _lock_for(user_id, project_id):
        return await _get_or_create_open_batch_locked(
            db, user_id=user_id, project_id=project_id,
            owner=owner, repo=repo, base_branch=base_branch, token=token,
        )


async def _get_or_create_open_batch_locked(
    db, *, user_id: str, project_id: str, owner: str, repo: str,
    base_branch: str, token: str,
) -> dict:
    """Same contract as `get_or_create_open_batch`, but assumes the
    caller already holds `_lock_for(user_id, project_id)`. Validation
    of user_id/project_id is still the public function's job — this
    one trusts its caller."""
    existing = await db[_COLLECTION].find_one(
        {"user_id": user_id, "project_id": project_id, "status": "open"},
    )
    if existing:
        if existing.get("fix_count", 0) >= MAX_FIXES_PER_BATCH:
            await db[_COLLECTION].update_one(
                {"batch_id": existing["batch_id"]},
                {"$set": {"status": "closed", "updated_at": time.time(),
                          "close_reason": "cap_reached"}},
            )
        else:
            verify = await _verify_batch_open_on_github(existing, owner=owner, repo=repo, token=token)
            if verify == "open":
                return {"ok": True, "batch": existing, "created": False}
            if verify == "lookup_failed":
                return {"ok": False, "error": "batch_verify_failed_retryable"}
            await db[_COLLECTION].update_one(
                {"batch_id": existing["batch_id"]},
                {"$set": {"status": verify, "updated_at": time.time()}},
            )

    return await _create_new_batch(
        db, user_id=user_id, project_id=project_id,
        owner=owner, repo=repo, base_branch=base_branch, token=token,
    )


async def _create_new_batch(
    db, *, user_id: str, project_id: str, owner: str, repo: str,
    base_branch: str, token: str,
) -> dict:
    from services.loop_safety import (
        add_pr_label,
        create_or_reuse_branch,
        open_or_reuse_pr,
    )

    branch = generate_batch_branch()
    ok_branch, branch_err = await create_or_reuse_branch(
        owner=owner, repo=repo, base_branch=base_branch,
        new_branch=branch, token=token,
    )
    if not ok_branch:
        # H2 — no direct-to-base fallback. A batch that can't get its
        # own branch is a hard failure, never a silent direct commit.
        return {"ok": False, "error": branch_err or "branch_create_failed"}

    pr_result = await open_or_reuse_pr(
        owner=owner, repo=repo, head_branch=branch, base_branch=base_branch,
        title=build_pr_title(0), body=build_pr_body([]), token=token,
    )
    if not pr_result.get("ok"):
        return {"ok": False, "error": pr_result.get("error") or "pr_open_failed"}

    try:
        await add_pr_label(
            owner=owner, repo=repo, pr_number=pr_result["pr_number"],
            label="aura:fix-batch", token=token,
        )
    except Exception as e:                                       # noqa: BLE001
        logger.debug("batch PR label add failed (non-fatal): %r", e)

    now = time.time()
    row = {
        "batch_id": f"fb_{uuid.uuid4().hex[:12]}",
        "user_id": user_id, "project_id": project_id,
        "branch": branch, "base_branch": base_branch,
        "pr_number": pr_result["pr_number"], "pr_url": pr_result["pr_url"],
        "status": "open", "fix_count": 0,
        "created_at": now, "updated_at": now,
    }
    try:
        await db[_COLLECTION].insert_one(row)
    except Exception as e:                                       # noqa: BLE001
        if "duplicate key" in str(e).lower() or "E11000" in str(e):
            winner = await db[_COLLECTION].find_one(
                {"user_id": user_id, "project_id": project_id, "status": "open"},
            )
            if winner:
                return {"ok": True, "batch": winner, "created": False}
        return {"ok": False, "error": f"batch_insert_failed: {e}"}
    return {"ok": True, "batch": row, "created": True}


async def record_fix_in_batch(
    db, *, batch_id: str, rule_id: str, file: str, line: Any,
    severity: str, commit_sha: str, status: str,
    owner: str, repo: str, token: str,
) -> None:
    """Increments the batch's fix_count, appends to its row list (kept
    on the batch doc so the PR body can be rebuilt from Mongo without
    an extra query), and PATCHes the PR body. Best-effort on the
    GitHub side — a PR-body-update failure must never undo or hide
    an already-landed commit."""
    now = time.time()
    batch = await db[_COLLECTION].find_one_and_update(
        {"batch_id": batch_id},
        {
            "$inc": {"fix_count": 1},
            "$push": {"rows": {
                "rule_id": rule_id, "file": file, "line": line,
                "severity": severity, "commit_sha": commit_sha,
                "status": status, "at": now,
            }},
            "$set": {"updated_at": now},
        },
        return_document=True,
    )
    if not batch:
        return
    pr_number = batch.get("pr_number")
    if not pr_number:
        return
    try:
        from services.loop_safety import update_pr_body
        rows = batch.get("rows") or []
        await update_pr_body(
            owner=owner, repo=repo, pr_number=pr_number,
            body=build_pr_body(rows), token=token,
        )
    except Exception as e:                                       # noqa: BLE001
        logger.warning("batch PR body update failed (non-fatal): %r", e)


async def record_fixes_in_batch(
    db, *, batch_id: str, rows: list[dict], owner: str, repo: str, token: str,
) -> None:
    """Part 2b F2 — multi-row variant of `record_fix_in_batch`, for
    `apply_file_fixes`'s one-commit-resolves-several-findings case.
    Each row in `rows` is {rule_id, file, line, severity, commit_sha,
    status}. Pushes all of them in ONE update + ONE PR-body PATCH,
    instead of N round-trips for N findings resolved by the same
    commit."""
    if not rows:
        return
    now = time.time()
    stamped = [{**r, "at": now} for r in rows]
    batch = await db[_COLLECTION].find_one_and_update(
        {"batch_id": batch_id},
        {
            "$inc": {"fix_count": len(stamped)},
            "$push": {"rows": {"$each": stamped}},
            "$set": {"updated_at": now},
        },
        return_document=True,
    )
    if not batch:
        return
    pr_number = batch.get("pr_number")
    if not pr_number:
        return
    try:
        from services.loop_safety import update_pr_body
        all_rows = batch.get("rows") or []
        await update_pr_body(
            owner=owner, repo=repo, pr_number=pr_number,
            body=build_pr_body(all_rows), token=token,
        )
    except Exception as e:                                       # noqa: BLE001
        logger.warning("batch PR body update failed (non-fatal): %r", e)


async def get_batch_by_id(db, *, batch_id: str, user_id: str, project_id: str) -> dict | None:
    """Ownership-scoped read — ALWAYS filters by the caller's own
    user_id + project_id, never by batch_id alone (IDOR guard)."""
    if not (isinstance(batch_id, str) and batch_id
            and isinstance(user_id, str) and user_id
            and isinstance(project_id, str) and project_id):
        return None
    return await db[_COLLECTION].find_one(
        {"batch_id": batch_id, "user_id": user_id, "project_id": project_id},
    )
