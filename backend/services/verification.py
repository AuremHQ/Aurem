"""
services/verification.py — Trust & Verification round, shared foundation
(Enhancements 1-3 of the six-item spec).

Shared, APPEND-ONLY decision ledger used by BOTH the Loop Mode
independent verifier (services/loop_independent_verifier.py) and the
new single-edit chat-task independent verifier
(services/task_independent_verifier.py), so every task-completion
claim in the system — regardless of which pipeline produced it — is
recorded in ONE place with a uniform shape. This is what makes the
trust-calibration metric (services/trust_calibration.py) and the
retroactive-downgrade history (services/task_downgrade.py) possible
without a second, competing schema.

Collection: `task_verifications`. A row is NEVER mutated in place —
only ever inserted. The full decision history for a task_id survives
every subsequent downgrade/re-verification. Legacy tasks that never
went through any of this (shipped before this round existed) simply
have zero rows here — `display_level(None)` correctly renders "Not
verified" for them; nothing fabricates historical verification.

Verification levels — describe the ENVIRONMENT and EXTENT of
evidence, never a blanket quality guarantee:
    SOURCE-INSPECTED    — code reviewed; not run
    SANDBOX-REPRODUCED  — tested in a sandbox; not confirmed live
    STAGING-VERIFIED    — verified in staging; not confirmed in prod
    PRODUCTION-VERIFIED — verified in production
Missing/unrecognized -> "Not verified".
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

COLLECTION = "task_verifications"

DECISION_PASS = "pass"
DECISION_FAIL = "fail"
DECISION_INCONCLUSIVE = "inconclusive"
DECISION_DOWNGRADED = "downgraded"     # retroactive-downgrade event, see task_downgrade.py
DECISION_VALUES = {DECISION_PASS, DECISION_FAIL, DECISION_INCONCLUSIVE}

LEVEL_SOURCE_INSPECTED = "SOURCE-INSPECTED"
LEVEL_SANDBOX_REPRODUCED = "SANDBOX-REPRODUCED"
LEVEL_STAGING_VERIFIED = "STAGING-VERIFIED"
LEVEL_PRODUCTION_VERIFIED = "PRODUCTION-VERIFIED"
VALID_LEVELS = {LEVEL_SOURCE_INSPECTED, LEVEL_SANDBOX_REPRODUCED,
                LEVEL_STAGING_VERIFIED, LEVEL_PRODUCTION_VERIFIED}

_LEVEL_COPY = {
    LEVEL_SOURCE_INSPECTED:    "Code reviewed; not run",
    LEVEL_SANDBOX_REPRODUCED:  "Tested in a sandbox; not confirmed live",
    LEVEL_STAGING_VERIFIED:    "Verified in staging; not confirmed in production",
    LEVEL_PRODUCTION_VERIFIED: "Verified in production",
}
NOT_VERIFIED_COPY = "Not verified"

POLICY_VERSION = "v1"


def display_level(level: Optional[str]) -> str:
    """Backend-authoritative mapping from the stored enum to the exact
    user-facing phrase from Enhancement 2's table. Missing/unrecognized
    -> "Not verified" — the client only ever renders this string, it
    never computes it (client-supplied levels are never authoritative)."""
    return _LEVEL_COPY.get((level or "").strip(), NOT_VERIFIED_COPY)


def compute_task_revision_id(task_text: str, files: dict) -> str:
    """Stable hash of (task description + every file's final content).
    Used to detect "evidence changed after verification" so a stale
    verdict can never be replayed against a newer revision of the same
    task_id — the hash is only equal when both the ask AND the shipped
    content are byte-identical to what was actually verified."""
    h = hashlib.sha256()
    h.update((task_text or "").encode("utf-8", "ignore"))
    for path in sorted((files or {}).keys()):
        h.update(b"\x00")
        h.update(path.encode("utf-8", "ignore"))
        h.update((files.get(path) or "").encode("utf-8", "ignore"))
    return h.hexdigest()[:32]


async def record_verification(
    db, *, task_id: str, project_id: Optional[str], user_id: Optional[str],
    task_revision_id: str, verified_by: str, executed_by: str,
    decision: str, reason: str, verification_level: Optional[str] = None,
    evidence_refs: Optional[list] = None, origin: str = "chat_task",
    policy_version: str = POLICY_VERSION,
) -> dict:
    """Append-only insert — never mutates a prior row.

    Enforces `verified_by != executed_by` (Enhancement 1's
    non-negotiable invariant) defensively AT THE WRITE CHOKE POINT,
    not just by convention at each call site: if a caller ever passes
    the same identity for both (a bug, or a spoofing attempt), the
    decision is force-downgraded to `inconclusive` and the reason is
    tagged, so a self-graded "pass" can never enter the ledger."""
    if decision not in DECISION_VALUES and decision != DECISION_DOWNGRADED:
        decision = DECISION_INCONCLUSIVE
    if verified_by and executed_by and verified_by == executed_by:
        logger.error(
            "verification.record_verification: REJECTED self-grading — "
            "verified_by == executed_by (%r) for task_id=%s.",
            verified_by, task_id,
        )
        decision = DECISION_INCONCLUSIVE
        reason = f"rejected_self_grading:{reason}"[:300]
        verification_level = None
    if verification_level is not None and verification_level not in VALID_LEVELS:
        verification_level = None
    row = {
        "verification_id":   uuid.uuid4().hex,
        "task_id":            task_id,
        "project_id":         project_id,
        "user_id":            user_id,
        "task_revision_id":   task_revision_id,
        "verified_by":        verified_by,
        "executed_by":        executed_by,
        "decision":           decision,
        "reason":             (reason or "")[:500],
        "verification_level": verification_level,
        "evidence_refs":      evidence_refs or [],
        "origin":             origin,
        "policy_version":     policy_version,
        "created_at":         time.time(),
    }
    if db is not None:
        try:
            await db[COLLECTION].insert_one(dict(row))
        except Exception as e:                                # noqa: BLE001
            logger.warning("record_verification insert failed: %r", e)
    return row


async def get_latest_verification(db, task_id: str) -> Optional[dict]:
    if db is None or not task_id:
        return None
    return await db[COLLECTION].find_one(
        {"task_id": task_id}, {"_id": 0}, sort=[("created_at", -1)],
    )


async def get_verification_history(db, task_id: str, limit: int = 50) -> list:
    if db is None or not task_id:
        return []
    cur = db[COLLECTION].find({"task_id": task_id}, {"_id": 0}) \
        .sort("created_at", -1).limit(limit)
    return [d async for d in cur]


async def ensure_indexes(db) -> None:
    if db is None:
        return
    await db[COLLECTION].create_index("task_id")
    await db[COLLECTION].create_index("task_revision_id")
    await db[COLLECTION].create_index("created_at")
    await db[COLLECTION].create_index("origin")
