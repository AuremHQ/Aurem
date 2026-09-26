"""
services/scope_confirmation.py — Trust & Verification round,
Enhancement 5 (Pre-Execution Scope Confirmation).

Closes a gap explicitly documented in
services/semantic_mode_router.py's own docstring: the
`classify_intent_v2` confidence/`needs_confirm` signal (computed on
EVERY chat message, independent of the semantic-router 10% canary —
see services/mode_classifier.py) was only ever broadcast as a UI
badge; nothing downstream actually gated dispatch on it.

This module gates exactly the real consequential-execution choke
point: the moment a turn is about to fall through into the general
tool-capable orchestrator (`_run_orchestrator` in
routers/chat/worker.py) — every mode with its OWN hard mutation gate
(Mode D's "Approve the fix" click) or that never mutates (A/B/E/F)
already short-circuited earlier in the same dispatch cascade. Read-
only classification/planning may still happen before this gate;
nothing that writes a file or opens a commit runs before it.

Persisted in its own collection (`scope_confirmation_proposals`) —
mirrors services/actions/pending_action.py's proven pattern (atomic
conditional transitions, TTL expiry, session/user/project binding)
rather than inventing a competing state-machine shape.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from pymongo import ReturnDocument

logger = logging.getLogger(__name__)

COLLECTION = "scope_confirmation_proposals"

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

TTL_S = 15 * 60  # 15 min — long enough for a user to notice and click


def _scope_summary(prompt: str, project_id: Optional[str], repo_scope=None) -> str:
    """Short, human-readable interpreted-scope summary. Deliberately
    NOT an LLM call — deterministic, so the summary itself can never
    become an unverified claim; it just echoes back what was asked."""
    target = f"project {project_id}" if project_id else "the connected project"
    if repo_scope:
        target = (f"{repo_scope['github_owner']}/{repo_scope['github_repo']} "
                  f"on branch {repo_scope['branch']}")
    trimmed = (prompt or "").strip()
    if len(trimmed) > 160:
        trimmed = trimmed[:160].rstrip() + "…"
    return (
        f"I read this as a request to make a CODE CHANGE in {target}: "
        f"\u201c{trimmed}\u201d. This wasn't clear enough for me to be "
        f"confident I've got the right scope, so I'm checking before "
        f"writing or committing anything."
    )


async def create_proposal(
    db, *, session_id: str, user_id: str, project_id: Optional[str],
    prompt: str, confidence: Optional[float], threshold: float,
    would_be_mode: str, repo_scope: dict | None = None,
) -> dict:
    now = time.time()
    doc = {
        "id":            uuid.uuid4().hex,
        "session_id":    session_id,
        "user_id":       user_id,
        "project_id":    project_id,
        "prompt":        prompt,
        "scope_summary": _scope_summary(prompt, project_id, repo_scope),
        "confidence":    confidence,
        "threshold":     threshold,
        "would_be_mode": would_be_mode,
        "repo_scope": repo_scope,
        "status":        STATUS_PENDING,
        "created_at":    now,
        "expires_at":    now + TTL_S,
    }
    if db is None:
        raise RuntimeError("Cannot save a confirmation proposal while storage is unavailable")
    # Never offer a confirmation ID that was not durably saved.
    await db[COLLECTION].insert_one(dict(doc))
    return doc


async def _get_live(db, proposal_id: str, user_id: str) -> Optional[dict]:
    if db is None or not proposal_id:
        return None
    doc = await db[COLLECTION].find_one(
        {"id": proposal_id, "user_id": user_id}, {"_id": 0},
    )
    if not doc:
        return None
    if doc["status"] == STATUS_PENDING and time.time() > doc.get("expires_at", 0):
        await db[COLLECTION].update_one(
            {"id": proposal_id, "status": STATUS_PENDING},
            {"$set": {"status": STATUS_EXPIRED}},
        )
        doc["status"] = STATUS_EXPIRED
    return doc


async def confirm_proposal(db, *, proposal_id: str, user_id: str, prompt: str) -> dict:
    """Confirmation authorizes ONLY the exact displayed scope version
    — the caller's CURRENT prompt must match the proposal's stored
    prompt verbatim, or the confirm is rejected (a material scope
    change requires a fresh confirmation, never a stale approval)."""
    if not proposal_id:
        return {"ok": False, "error": "no_proposal_id"}
    doc = await _get_live(db, proposal_id, user_id)
    if not doc:
        return {"ok": False, "error": "proposal_not_found"}
    if doc["status"] != STATUS_PENDING:
        return {"ok": False, "error": f"proposal_not_pending:{doc['status']}"}
    if (doc.get("prompt") or "") != (prompt or ""):
        return {"ok": False, "error": "scope_changed_needs_new_confirmation"}
    updated = await db[COLLECTION].find_one_and_update(
        {"id": proposal_id, "status": STATUS_PENDING},
        {"$set": {"status": STATUS_CONFIRMED, "confirmed_at": time.time()}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        # Raced with a cancel/expiry between the read above and now.
        return {"ok": False, "error": "proposal_no_longer_pending"}
    updated.pop("_id", None)
    return {"ok": True, "proposal": updated}


async def cancel_proposal(db, *, proposal_id: str, user_id: str,
                          reason: str = "user_cancelled") -> dict:
    doc = await _get_live(db, proposal_id, user_id)
    if not doc:
        return {"ok": False, "error": "proposal_not_found"}
    if doc["status"] != STATUS_PENDING:
        return {"ok": True, "already": True, "status": doc["status"]}
    update = await db[COLLECTION].update_one(
        {"id": proposal_id, "user_id": user_id, "status": STATUS_PENDING},
        {"$set": {"status": STATUS_CANCELLED, "cancel_reason": reason,
                  "cancelled_at": time.time()}},
    )
    if not update.modified_count:
        latest = await _get_live(db, proposal_id, user_id)
        return {"ok": False, "already": True, "status": (latest or {}).get("status", "unknown")}
    return {"ok": True, "already": False, "status": STATUS_CANCELLED}


async def ensure_indexes(db) -> None:
    if db is None:
        return
    await db[COLLECTION].create_index("id")
    await db[COLLECTION].create_index([("session_id", 1), ("created_at", -1)])
