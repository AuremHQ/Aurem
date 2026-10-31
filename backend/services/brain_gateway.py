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

# Part 4, F1 — rows that never made it to Mongo: either record_call's
# own enqueue (full queue / internal error) or a drained row whose
# Mongo write itself failed. Visibility only (GET /api/admin/brain/
# health) — never read for control flow.
_dropped_count = 0

# Hard-off. Flipping this is a separate, explicitly-reviewed change —
# this module never does it itself.
_TEXT_STORAGE_ENABLED = False

# Outcome values objective signals can report. The model NEVER grades
# itself — every value here comes from an external hook (test runner,
# PR-merge webhook, user click), never from the LLM's own output.
_STRONG_POSITIVE_OUTCOMES = frozenset({
    "tests_passed", "verification_passed", "pr_merged", "user_approved",
})
# Part 4, F2 precedence rule — a NEGATIVE outcome arriving AFTER a
# positive one always overrides it and demotes any promotion that
# positive outcome already caused (promoted -> "revoked"). The first
# POSITIVE outcome still just sticks (first-write-wins) if no
# negative ever follows.
_NEGATIVE_OUTCOMES = frozenset({"rolled_back", "user_rejected", "tests_failed"})
_OUTCOME_VALUES = _STRONG_POSITIVE_OUTCOMES | _NEGATIVE_OUTCOMES | frozenset({
    "no_rollback",
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
    than once (no-op if already running).

    Part 4, F1 fix: this used to call asyncio.ensure_future(
    _drain_forever()) WITHOUT first ensuring `_write_queue` exists.
    _drain_forever's first line was `await _write_queue.get()` on the
    bare module global — if start_worker() ran before any
    record_call() had ever created the queue (exactly what main.py's
    "start the worker 38s after boot" ordering does), `_write_queue`
    was still None and `_drain_forever` died immediately on
    `AttributeError: 'NoneType' object has no attribute 'get'`,
    silently, with nothing written until the next process restart.
    `_ensure_queue()` here closes that window."""
    global _worker_task
    _ensure_queue()
    if _worker_task is not None and not _worker_task.done():
        return
    try:
        _worker_task = asyncio.ensure_future(_drain_forever())
    except RuntimeError:
        pass  # no running loop yet — harmless, startup will retry


def _restart_worker_if_dead() -> None:
    """Called from record_call's hot path — cheap (one attribute
    check) so a worker that died for ANY reason (even a future bug
    this file doesn't yet guard against) self-heals on the very next
    recorded call instead of staying dead until a process restart."""
    if _worker_task is None or _worker_task.done():
        start_worker()


async def _write_one(row: dict) -> None:
    global _dropped_count
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is None:
            return
        await asyncio.wait_for(
            db[_COLLECTION].insert_one(row), timeout=_WRITE_TIMEOUT_SECONDS,
        )
    except Exception as e:                                      # noqa: BLE001
        _dropped_count += 1
        logger.debug("brain_gateway write dropped sample: %r", e)


async def _drain_forever() -> None:
    global _dropped_count
    while True:
        # Re-ensure every iteration, not just once at start_worker():
        # if the queue is ever None (F1's original bug — a worker
        # launched before any record_call had created it), this
        # creates a real, empty Queue and `.get()` below just awaits
        # normally rather than crashing on `None.get()`. Cheap (one
        # attribute check) when the queue already exists.
        queue = _ensure_queue()
        try:
            row = await queue.get()
            await _write_one(row)
        except Exception as e:                                  # noqa: BLE001
            # Outer net around BOTH the queue.get() and the write —
            # _write_one already catches its own errors, but this is
            # a second layer so a bug (a test monkeypatch that
            # replaces _write_one wholesale, an unexpected exception
            # type) can never kill the background worker permanently
            # and stop future drains.
            _dropped_count += 1
            logger.debug("brain_gateway drain_forever swallowed: %r", e)


async def drain_once_for_tests() -> bool:
    """Pulls and writes exactly one queued row, synchronously, for
    deterministic tests — the real path uses the detached background
    worker instead. Returns False if the queue is empty/unset. Never
    raises, same outer-net guarantee as _drain_forever."""
    global _dropped_count
    if _write_queue is None or _write_queue.empty():
        return False
    row = await _write_queue.get()
    try:
        await _write_one(row)
    except Exception as e:                                       # noqa: BLE001
        _dropped_count += 1
        logger.debug("brain_gateway drain_once_for_tests swallowed: %r", e)
    return True


def dropped_count() -> int:
    """Rows that never made it to Mongo (enqueue rejected, or a
    drained write ultimately failed). Read-only — never used for
    control flow, only for GET /api/admin/brain/health visibility."""
    return _dropped_count


def queue_size() -> int:
    """Current backlog in the write queue. 0 (not an error) when the
    queue hasn't been created yet — nothing has ever been recorded."""
    return _write_queue.qsize() if _write_queue is not None else 0


def worker_alive() -> bool:
    """Whether the background drain task is currently running. False
    (not an error) before the first record_call/start_worker call."""
    return _worker_task is not None and not _worker_task.done()


async def get_health_snapshot(db) -> dict:
    """Part 4, F7 — admin-only visibility into the gateway's own
    operational state: local-process counters (queue/worker/dropped,
    never gated on DB access) plus a best-effort DB-side summary of
    the last 24h. Never raises — any DB error degrades the DB-backed
    fields to 0/None rather than failing the whole snapshot."""
    from services.knowledge_injector import knowledge_mode
    snapshot = {
        "queue_size": queue_size(),
        "worker_alive": worker_alive(),
        "dropped_count": dropped_count(),
        "knowledge_mode": knowledge_mode(),
        "rows_last_24h": 0,
        "rows_with_outcome_last_24h": 0,
        "outcome_pct_last_24h": 0.0,
        "candidate_count": 0,
    }
    if db is None:
        return snapshot
    try:
        since = time.time() - 86400
        total = await db[_COLLECTION].count_documents({"created_at": {"$gte": since}})
        with_outcome = await db[_COLLECTION].count_documents(
            {"created_at": {"$gte": since}, "outcome": {"$ne": None}},
        )
        candidates = await db[_COLLECTION].count_documents({"promoted": "candidate"})
        snapshot["rows_last_24h"] = total
        snapshot["rows_with_outcome_last_24h"] = with_outcome
        snapshot["outcome_pct_last_24h"] = round((with_outcome / total) * 100, 1) if total else 0.0
        snapshot["candidate_count"] = candidates
    except Exception as e:                                       # noqa: BLE001
        logger.debug("get_health_snapshot DB summary failed: %r", e)
    return snapshot


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
    task_id: str | None = None,
) -> str | None:
    """Enqueues one brain_calls row. Never blocks on Mongo, never
    raises. Returns the call_id (for a later attach_outcome call) or
    None if the row was dropped (full queue, or any internal error) —
    callers never need to branch on this.

    Part 4, F2 — `task_id` (when the caller has one, e.g. from
    services.loop_token_ledger.current_surface_context()) lets a
    later outcome event find and update EVERY brain_calls row for
    that task in one shot via attach_outcome_for_task, instead of
    needing to thread call_id through to wherever the outcome is
    eventually known (often a different request entirely — a
    webhook, a background verifier)."""
    global _dropped_count
    try:
        _restart_worker_if_dead()
        call_id = f"bc_{uuid.uuid4().hex[:16]}"
        row: dict[str, Any] = {
            "call_id":         call_id,
            "user_id":         user_id,
            "project_id":      project_id,
            "task_type":       task_type,
            "task_id":         task_id,
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
        _dropped_count += 1
        logger.debug("brain_gateway queue full, sample dropped")
        return None
    except Exception as e:                                       # noqa: BLE001
        _dropped_count += 1
        logger.debug("brain_gateway record_call failed: %r", e)
        return None


# ───────────────────────────── outcome attach ─────────────────────────────
def _fire_promotion(db, user_id: str | None) -> None:
    """Off the hot path — promotion is best-effort and must never
    delay or break the outcome-attach call that triggered it. Fires
    a detached task; any failure inside is caught and logged, never
    propagated back to the attach caller."""
    if not user_id:
        return

    async def _run():
        try:
            await promote_to_candidate(db, user_id)
        except Exception as e:                                  # noqa: BLE001
            logger.debug("background promote_to_candidate failed: %r", e)
    try:
        asyncio.ensure_future(_run())
    except RuntimeError:
        pass  # no running loop — acceptable in a sync test context


async def _demote_revoked_rows(db, rows: list[dict]) -> None:
    """A row that WAS promoted="candidate" has its note pulled back
    OUT of that user's project_memory and is marked "revoked" so
    export_anonymized_global_draft (which only ever reads
    promoted="candidate") never surfaces it again. `rows` must be the
    PRE-update snapshot (captured before the outcome changed) so the
    note text reconstructed here matches exactly what
    promote_to_candidate originally wrote."""
    if not rows:
        return
    from services.project_memory import remove_fixes_that_worked_note
    for row in rows:
        if row.get("promoted") != "candidate":
            continue
        user_id, project_id = row.get("user_id"), row.get("project_id")
        note = f"{row.get('task_type') or 'task'} succeeded ({row.get('outcome')})"
        if user_id and project_id:
            try:
                await remove_fixes_that_worked_note(db, user_id, project_id, note)
            except Exception as e:                              # noqa: BLE001
                logger.debug("demote: note removal failed: %r", e)
        try:
            await db[_COLLECTION].update_one(
                {"call_id": row.get("call_id")}, {"$set": {"promoted": "revoked"}},
            )
        except Exception as e:                                  # noqa: BLE001
            logger.debug("demote: mark revoked failed: %r", e)


# ───────────────────── Part 4, F5 — side-door record wrap ────────────────
async def record_wrap(
    coro,
    *,
    user_id: str | None,
    project_id: str | None = None,
    task_id: str | None = None,
    task_type: str = "direct",
    model: str | None = None,
    surface: str = "side_door",
):
    """One shared helper for the direct-provider callers that bypass
    the 3 main entry points (routers/chat/worker.py, services/
    agents.py, services/graph_builder.py, services/
    vanguard_verify_agent.py, services/web_inspect.py, routers/
    chat/misc.py, services/video_pipeline/house_rules_check.py — see
    the Part 4 guard test's allow-list). RECORD ONLY: no routing
    change, no context injection — `coro` is awaited exactly as the
    caller built it, and whatever it returns or raises propagates
    completely unchanged. The ONLY side effect is one best-effort
    brain_calls row; a failure recording it is swallowed, never
    re-raised, and never delays the result (fail-open, same
    guarantee as record_call itself)."""
    t0 = time.monotonic()
    try:
        result = await coro
    except Exception:
        try:
            await record_call(
                user_id=user_id, project_id=project_id, task_id=task_id,
                task_type=task_type, model=model,
                latency_ms=round((time.monotonic() - t0) * 1000, 1),
                surface=surface,
            )
        except Exception as e:                                  # noqa: BLE001
            logger.debug("record_wrap record-on-error skipped: %r", e)
        raise
    try:
        await record_call(
            user_id=user_id, project_id=project_id, task_id=task_id,
            task_type=task_type, model=model,
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
            surface=surface,
        )
    except Exception as e:                                      # noqa: BLE001
        logger.debug("record_wrap record skipped: %r", e)
    return result


async def attach_outcome(db, call_id: str, outcome: str, *, user_id: str | None = None) -> bool:
    """Attaches an outcome to ONE brain_calls row (by call_id).

    Precedence (Part 4, F2): a POSITIVE outcome only sets if the row
    doesn't already have one (atomic `outcome: None` filter — race-
    safe, same guarantee as before). A NEGATIVE outcome (rolled_back/
    user_rejected/tests_failed) ALWAYS overrides, even a positive
    one already stuck, and demotes any promotion it caused. Cross-
    user isolation: when `user_id` is given, only a row that ALREADY
    belongs to that user_id can be touched — a call_id alone is
    never enough once ownership is being asserted.

    Idempotent: re-attaching the SAME outcome is a safe no-op."""
    if (
        db is None or not isinstance(call_id, str) or not call_id
        or not isinstance(outcome, str) or outcome not in _OUTCOME_VALUES
    ):
        return False
    base_filter: dict[str, Any] = {"call_id": call_id}
    if user_id:
        base_filter["user_id"] = user_id
    try:
        existing = await db[_COLLECTION].find_one(base_filter)
    except Exception as e:                                       # noqa: BLE001
        logger.debug("attach_outcome lookup failed: %r", e)
        return False
    if not existing:
        return False  # not found, or user_id mismatch — IDOR-safe no-op

    try:
        if outcome in _NEGATIVE_OUTCOMES:
            res = await db[_COLLECTION].update_one(
                base_filter, {"$set": {"outcome": outcome, "outcome_at": time.time()}},
            )
            changed = bool(getattr(res, "matched_count", 0))
            if changed:
                await _demote_revoked_rows(db, [existing])
            return changed
        res = await db[_COLLECTION].update_one(
            {**base_filter, "outcome": None},
            {"$set": {"outcome": outcome, "outcome_at": time.time()}},
        )
        changed = bool(getattr(res, "matched_count", 0))
        if changed and outcome in _STRONG_POSITIVE_OUTCOMES:
            _fire_promotion(db, existing.get("user_id"))
        return changed
    except Exception as e:                                       # noqa: BLE001
        logger.debug("attach_outcome failed: %r", e)
        return False


async def attach_outcome_for_task(
    db, task_id: str, outcome: str, *, user_id: str | None = None,
) -> int:
    """Attaches an outcome to EVERY brain_calls row for `task_id` in
    one shot — the common case, since a webhook or a verifier knows
    the task, not any individual call_id. Same precedence rule as
    attach_outcome, applied per-row. Cross-user isolation: when
    `user_id` is given, only rows whose OWN user_id matches are ever
    touched — the webhook/verifier caller is responsible for
    resolving the real owning user_id FIRST (e.g. from the task/
    ship_branch record) rather than trusting a client-supplied one.
    Returns the number of rows changed."""
    if (
        db is None or not isinstance(task_id, str) or not task_id
        or not isinstance(outcome, str) or outcome not in _OUTCOME_VALUES
    ):
        return 0
    base_filter: dict[str, Any] = {"task_id": task_id}
    if user_id:
        base_filter["user_id"] = user_id
    try:
        cur = db[_COLLECTION].find(base_filter)
        existing_rows = [r async for r in cur]
    except Exception as e:                                       # noqa: BLE001
        logger.debug("attach_outcome_for_task lookup failed: %r", e)
        return 0
    if not existing_rows:
        return 0

    try:
        if outcome in _NEGATIVE_OUTCOMES:
            res = await db[_COLLECTION].update_many(
                base_filter, {"$set": {"outcome": outcome, "outcome_at": time.time()}},
            )
            changed = getattr(res, "modified_count", None)
            if changed is None:
                changed = getattr(res, "matched_count", len(existing_rows))
            if changed:
                await _demote_revoked_rows(db, existing_rows)
            return changed
        res = await db[_COLLECTION].update_many(
            {**base_filter, "outcome": None},
            {"$set": {"outcome": outcome, "outcome_at": time.time()}},
        )
        changed = getattr(res, "modified_count", None)
        if changed is None:
            changed = getattr(res, "matched_count", 0)
        if changed and outcome in _STRONG_POSITIVE_OUTCOMES:
            _fire_promotion(db, existing_rows[0].get("user_id"))
        return changed
    except Exception as e:                                       # noqa: BLE001
        logger.debug("attach_outcome_for_task failed: %r", e)
        return 0


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
        # Part 4, F2 — outcome-by-task lookups (attach_outcome_for_task).
        await db[_COLLECTION].create_index(
            [("task_id", 1)], name="ix_bc_task_id",
        )
        await db[_COLLECTION].create_index(
            [("user_id", 1), ("task_id", 1)], name="ix_bc_user_task",
        )
    except Exception as e:                                        # noqa: BLE001
        logger.warning("brain_gateway ensure_indexes failed: %r", e)
