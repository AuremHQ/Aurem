"""
services/brain_gateway.py — ORA Brain Part 3, Phase E (Step 1: RECORD ONLY).

A thin wrapper used INSIDE the 3 main LLM entry points:
  - services/llm/_meta.py::call_llm_with_meta
  - services/llm/__init__.py::call_llm
  - core/parliament/llm_call.py::_llm_call_protected

Step 1 scope is RECORD ONLY: before this module existed, nothing
changed about an LLM call; after, one row is written to `brain_calls`
per logical call. No routing change, no context injection, no
self-grading. This sits on every LLM call in the app, so fail-open is
mandatory: a gateway failure (bad Mongo, missing secret, bug in this
file) must NEVER break or delay the underlying LLM call.

Fail-open mechanics
--------------------
- `record_call()` never awaits Mongo on the hot path. It does one
  `asyncio.Queue.put_nowait` (O(1), cannot block) and returns. A
  lazily-started background task drains the bounded queue and writes
  to Mongo with its own per-item timeout; a full queue or any
  exception there just drops the sample (logged, never raised).
- Every public function in this module catches its own exceptions
  and degrades to a no-op — callers do not need their own try/except,
  though the 3 entry points keep one anyway (repo convention, and
  belt-and-suspenders for a module this central).

No raw prompt/response text is stored by default — only an
HMAC-SHA256 digest of the prompt (never a plain hash: a plain hash of
a short, somewhat-guessable prompt is dictionary-attackable; HMAC
needs a server-only secret that is itself never logged or stored) and
its size in characters. Full-text storage is a separate, hardcoded-off
flag (`_TEXT_STORAGE_ENABLED`) — flipping it is explicitly out of
scope for this change.

Double-count guard
-------------------
`core/parliament/llm_call.py::_llm_call_protected` calls
`services.llm.call_llm_with_meta` internally — so, unguarded, a
Parliament-sourced request would produce TWO rows for one logical LLM
round trip. `claim_recording()` is a ContextVar-based claim (same
idiom as `services.loop_token_ledger.llm_surface_context`): whichever
entry point enters it first "owns" the record for this call chain; a
nested entry point sees the claim already held and skips its own
`record_call`. The underlying LLM call itself is never affected by
who claims it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

_COLLECTION = "brain_calls"

_already_recorded_ctx: ContextVar[bool] = ContextVar(
    "brain_gateway.already_recorded", default=False,
)

# Fail-open bounds — a slow/broken Mongo must never stall a request.
_WRITE_TIMEOUT_SECONDS = 1.5
_QUEUE_MAXSIZE = 500
_write_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None

# Hard-off. Flipping this is a separate, explicitly-reviewed change —
# this module never does it itself.
_TEXT_STORAGE_ENABLED = False

# Outcome values objective signals can report. The model NEVER grades
# itself — every value here comes from an external hook (test runner,
# PR-merge webhook, user click), never from the LLM's own output.
_STRONG_POSITIVE_OUTCOMES = frozenset({
    "tests_passed", "verification_passed", "pr_merged", "user_approved",
})
_OUTCOME_VALUES = _STRONG_POSITIVE_OUTCOMES | frozenset({
    "no_rollback", "user_rejected", "rolled_back", "tests_failed",
})


# ─────────────────────────── claim + record ──────────────────────────────
@asynccontextmanager
async def claim_recording():
    """Yields True if an OUTER call already claimed the record for
    this call chain (so this level must skip its own record_call),
    False if this level is the owner. Never raises."""
    already = _already_recorded_ctx.get()
    token = _already_recorded_ctx.set(True)
    try:
        yield already
    finally:
        _already_recorded_ctx.reset(token)


def _secret() -> bytes | None:
    raw = os.environ.get("ORA_BRAIN_HMAC_SECRET")
    return raw.encode("utf-8") if raw else None


def _prompt_hmac(system: str, user: str) -> str | None:
    """HMAC-SHA256 of system+user, keyed by a server-only env secret.
    Returns None (not a fallback plain hash) when no secret is
    configured — an unkeyed digest would defeat the whole point."""
    secret = _secret()
    if not secret:
        return None
    try:
        digest = hmac.new(
            secret,
            (system or "").encode("utf-8") + b"\x00" + (user or "").encode("utf-8"),
            hashlib.sha256,
        )
        return digest.hexdigest()
    except Exception as e:                                      # noqa: BLE001
        logger.debug("brain_gateway hmac failed: %r", e)
        return None


def _ensure_queue() -> asyncio.Queue:
    global _write_queue
    if _write_queue is None:
        _write_queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    return _write_queue


def start_worker() -> None:
    """Starts the detached background drain task. Called once from
    main.py's lifespan — never implicitly from record_call, so tests
    can call drain_once_for_tests() deterministically without a
    worker racing them for the same queued row. Safe to call more
    than once (no-op if already running)."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return
    try:
        _worker_task = asyncio.ensure_future(_drain_forever())
    except RuntimeError:
        pass  # no running loop yet — harmless, startup will retry


async def _write_one(row: dict) -> None:
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is None:
            return
        await asyncio.wait_for(
            db[_COLLECTION].insert_one(row), timeout=_WRITE_TIMEOUT_SECONDS,
        )
    except Exception as e:                                      # noqa: BLE001
        logger.debug("brain_gateway write dropped sample: %r", e)


async def _drain_forever() -> None:
    while True:
        row = await _write_queue.get()
        try:
            await _write_one(row)
        except Exception as e:                                  # noqa: BLE001
            # _write_one already catches its own errors; this is a
            # second, outer net so a bug (or a test monkeypatch that
            # replaces _write_one wholesale) can never kill the
            # background worker permanently and stop future drains.
            logger.debug("brain_gateway drain_forever swallowed: %r", e)


async def drain_once_for_tests() -> bool:
    """Pulls and writes exactly one queued row, synchronously, for
    deterministic tests — the real path uses the detached background
    worker instead. Returns False if the queue is empty/unset. Never
    raises, same outer-net guarantee as _drain_forever."""
    if _write_queue is None or _write_queue.empty():
        return False
    row = await _write_queue.get()
    try:
        await _write_one(row)
    except Exception as e:                                       # noqa: BLE001
        logger.debug("brain_gateway drain_once_for_tests swallowed: %r", e)
    return True


async def record_call(
    *,
    user_id: str | None,
    project_id: str | None,
    task_type: str | None,
    model: str | None,
    latency_ms: float | None,
    tokens: Any = None,
    knowledge_files: list | None = None,
    system: str = "",
    user: str = "",
    surface: str = "unknown",
) -> str | None:
    """Enqueues one brain_calls row. Never blocks on Mongo, never
    raises. Returns the call_id (for a later attach_outcome call) or
    None if the row was dropped (full queue, or any internal error) —
    callers never need to branch on this."""
    try:
        call_id = f"bc_{uuid.uuid4().hex[:16]}"
        row: dict[str, Any] = {
            "call_id":         call_id,
            "user_id":         user_id,
            "project_id":      project_id,
            "task_type":       task_type,
            "model":           model,
            "latency_ms":      latency_ms,
            "tokens":          tokens,
            "knowledge_files": list(knowledge_files or []),
            "outcome":         None,
            "prompt_hmac":     _prompt_hmac(system, user),
            "prompt_size":     len(system or "") + len(user or ""),
            "surface":         surface,
            "promoted":        "raw",
            "created_at":      time.time(),
        }
        _ensure_queue().put_nowait(row)
        return call_id
    except asyncio.QueueFull:
        logger.debug("brain_gateway queue full, sample dropped")
        return None
    except Exception as e:                                       # noqa: BLE001
        logger.debug("brain_gateway record_call failed: %r", e)
        return None


# ───────────────────────────── outcome attach ─────────────────────────────
async def attach_outcome(db, call_id: str, outcome: str) -> bool:
    """Atomically attaches an outcome to a brain_calls row, ONLY if it
    doesn't already have one. Race-safe: the Mongo filter requires
    `outcome: None`, so of two concurrent attach_outcome calls for the
    SAME call_id, only the one that wins the atomic update actually
    sets it — the loser gets matched_count == 0 and returns False,
    never a corrupted/mixed value. Idempotent: attaching the SAME
    call_id a second time (any outcome) is a safe no-op, returns
    False without raising."""
    if db is None or not call_id or outcome not in _OUTCOME_VALUES:
        return False
    try:
        res = await db[_COLLECTION].update_one(
            {"call_id": call_id, "outcome": None},
            {"$set": {"outcome": outcome, "outcome_at": time.time()}},
        )
        return bool(getattr(res, "matched_count", 0))
    except Exception as e:                                       # noqa: BLE001
        logger.debug("attach_outcome failed: %r", e)
        return False


# ───────────────────────── promotion tier 2 — candidate ───────────────────
async def promote_to_candidate(db, user_id: str, limit: int = 5) -> int:
    """Tier 2: reads this user's OWN strong-outcome brain_calls rows
    (never another user's) and appends a short, size-capped note into
    THAT SAME user's project_memory (services.project_memory —
    already untrusted-labeled there). Never skips to tier 3: this
    function never touches backend/knowledge/ and never looks at any
    other user's rows. Returns the number of rows promoted (marked
    `promoted="candidate"` so they are never promoted twice)."""
    if db is None or not user_id:
        return 0
    try:
        cur = db[_COLLECTION].find(
            {"user_id": user_id, "outcome": {"$in": list(_STRONG_POSITIVE_OUTCOMES)},
             "promoted": "raw"},
            {"_id": 0, "call_id": 1, "task_type": 1, "outcome": 1, "project_id": 1},
        ).sort("created_at", -1).limit(max(0, limit))
        rows = [r async for r in cur]
    except Exception as e:                                        # noqa: BLE001
        logger.debug("promote_to_candidate query failed: %r", e)
        return 0
    if not rows:
        return 0
    from services.project_memory import upsert_project_memory
    promoted = 0
    for row in rows:
        project_id = row.get("project_id")
        if not project_id:
            continue
        note = f"{row.get('task_type') or 'task'} succeeded ({row.get('outcome')})"
        ok = await upsert_project_memory(
            db, user_id, project_id, {"fixes_that_worked": [note]},
        )
        if not ok:
            continue
        try:
            await db[_COLLECTION].update_one(
                {"call_id": row["call_id"]}, {"$set": {"promoted": "candidate"}},
            )
        except Exception as e:                                    # noqa: BLE001
            logger.debug("promote_to_candidate mark failed: %r", e)
        promoted += 1
    return promoted


# ───────────────── promotion tier 3 — anonymized global draft ─────────────
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PATH_RE = re.compile(r"(?:/[\w.\-]+){2,}|[A-Za-z]:\\\\(?:[\w.\-]+\\\\)+[\w.\-]+")
_REPO_RE = re.compile(r"\b[\w.-]+/[\w.-]+\.git\b|\bgithub\.com/[\w.-]+/[\w.-]+\b")
_SECRET_RE = re.compile(
    r"\bsk-[A-Za-z0-9]{10,}\b|\bghp_[A-Za-z0-9]{10,}\b|\bAKIA[0-9A-Z]{12,}\b"
    r"|\bBearer\s+[A-Za-z0-9._-]{10,}\b",
    re.IGNORECASE,
)


def anonymize_text(text: str) -> str:
    """Strips repo names, file paths, emails, keys/tokens from a
    draft string before it is ever shown to an admin as a candidate
    global-knowledge entry. Never raises — a non-str input returns
    "" rather than crashing the export."""
    if not isinstance(text, str):
        return ""
    out = _EMAIL_RE.sub("[redacted-email]", text)
    out = _SECRET_RE.sub("[redacted-secret]", out)
    out = _REPO_RE.sub("[redacted-repo]", out)
    out = _PATH_RE.sub("[redacted-path]", out)
    return out


async def export_anonymized_global_draft(db, limit: int = 20) -> list[dict]:
    """Tier 3: builds an ADMIN-ONLY, ANONYMIZED draft of candidate
    global-knowledge entries from Tier-2 rows across ALL users. NEVER
    writes to backend/knowledge/ — returns the draft in memory for a
    human (the founder) to review and manually approve. Caller (the
    admin router) is responsible for the actual admin-only gate; this
    function has no user/session concept, only db access."""
    if db is None:
        return []
    try:
        cur = db[_COLLECTION].find(
            {"promoted": "candidate"},
            {"_id": 0, "call_id": 1, "task_type": 1, "outcome": 1, "model": 1},
        ).sort("created_at", -1).limit(max(0, limit))
        rows = [r async for r in cur]
    except Exception as e:                                        # noqa: BLE001
        logger.debug("export_anonymized_global_draft query failed: %r", e)
        return []
    draft = []
    for row in rows:
        entry = {
            "task_type": anonymize_text(row.get("task_type") or ""),
            "outcome":   row.get("outcome"),
            "model":     anonymize_text(row.get("model") or ""),
            "note": anonymize_text(
                f"{row.get('task_type')} tasks succeeded with outcome "
                f"{row.get('outcome')} using {row.get('model')}",
            ),
        }
        draft.append(entry)
    return draft


# ───────────────────────────── scoreboard ──────────────────────────────
async def get_model_scoreboard(db) -> list[dict]:
    """Read-only admin report: outcome counts per (task_type, model).
    Never changes routing — purely observational."""
    if db is None:
        return []
    try:
        pipeline = [
            {"$match": {"outcome": {"$ne": None}}},
            {"$group": {
                "_id": {"task_type": "$task_type", "model": "$model", "outcome": "$outcome"},
                "count": {"$sum": 1},
            }},
        ]
        cur = db[_COLLECTION].aggregate(pipeline)
        rows = [r async for r in cur]
    except Exception as e:                                        # noqa: BLE001
        logger.debug("get_model_scoreboard failed: %r", e)
        return []
    out = []
    for r in rows:
        key = r.get("_id", {})
        out.append({
            "task_type": key.get("task_type"),
            "model": key.get("model"),
            "outcome": key.get("outcome"),
            "count": r.get("count", 0),
        })
    return out


# ──────────────────────────── delete-on-request ───────────────────────────
async def delete_own_brain_calls(db, user_id: str, project_id: str | None = None) -> bool:
    """Deletes only the caller's own brain_calls rows (optionally
    scoped further to one project_id). Idempotent — a second delete
    with nothing left to remove still returns True."""
    if db is None or not user_id:
        return False
    filt: dict[str, Any] = {"user_id": user_id}
    if project_id:
        filt["project_id"] = project_id
    try:
        await db[_COLLECTION].delete_many(filt)
        return True
    except Exception as e:                                        # noqa: BLE001
        logger.debug("delete_own_brain_calls failed: %r", e)
        return False


async def ensure_indexes(db) -> None:
    """Idempotent, best-effort — mirrors services/ora_fix_learning.py.
    Never blocks or breaks startup."""
    if db is None:
        return
    try:
        await db[_COLLECTION].create_index(
            [("call_id", 1)], unique=True, name="ix_bc_call_id",
        )
        await db[_COLLECTION].create_index(
            [("user_id", 1), ("created_at", -1)], name="ix_bc_user_ts",
        )
        await db[_COLLECTION].create_index(
            [("task_type", 1), ("model", 1), ("outcome", 1)], name="ix_bc_scoreboard",
        )
    except Exception as e:                                        # noqa: BLE001
        logger.warning("brain_gateway ensure_indexes failed: %r", e)
