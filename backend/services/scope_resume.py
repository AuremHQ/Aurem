"""One saved-proposal resolution/claim/receipt contract for button and typed approval.

No provider calls here. Consent is scoped to user/session/project/repository/branch.
Retries of an executing or uncertain proposal never initiate another task.
"""
import re
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel
from pymongo import ReturnDocument

from services.scope_confirmation import COLLECTION

_CONFIRM = re.compile(r"^confirm\s+and\s+proceed[.!]?$", re.IGNORECASE)
_SHA = re.compile(r"^[0-9a-f]{40,64}$", re.IGNORECASE)
STARTABLE = ("pending", "confirmed")


def is_scope_confirmation(text: str) -> bool:
    return bool(_CONFIRM.fullmatch((text or "").strip()))


def project_scope(project: dict) -> dict:
    return {k: project.get(k) for k in (
        "project_id", "github_owner", "github_repo", "branch", "installation_id",
    )}


class ScopeReceipt(BaseModel):
    proposal_id: str
    status: str
    mode: str
    task_id: str | None = None
    commit_sha: str | None = None
    request_id: str | None = None
    error: str | None = None


async def resolve_proposal(db, *, user_id, session_id, project_id, prompt, proposal_id=None):
    if db is None:
        raise HTTPException(503, "Confirmation storage is unavailable. Nothing was started.")
    if not session_id or not project_id or project_id == "home":
        raise HTTPException(409, "Open the original project and conversation before confirming.")
    where = {"user_id": user_id, "session_id": session_id, "project_id": project_id}
    if proposal_id:
        doc = await db[COLLECTION].find_one({**where, "id": proposal_id}, {"_id": 0})
    else:
        pending = await db[COLLECTION].find(
            {**where, "status": {"$in": list(STARTABLE)}, "expires_at": {"$gt": time.time()}},
            {"_id": 0},
        ).sort("created_at", -1).limit(2).to_list(2)
        if len(pending) > 1:
            raise HTTPException(409, "More than one proposal is pending. Use the button on the intended proposal.")
        doc = pending[0] if pending else await db[COLLECTION].find_one(
            where, {"_id": 0}, sort=[("created_at", -1)],
        )
    if not doc:
        raise HTTPException(404, "No saved scope proposal was found in this conversation. Nothing was started.")
    if not is_scope_confirmation(prompt) and prompt != doc.get("prompt"):
        raise HTTPException(409, "The requested scope changed. Send the revised request for a new proposal.")
    if doc.get("status") in ("cancelled", "expired"):
        raise HTTPException(409, "This proposal was cancelled or expired. Send a new request.")
    if doc.get("status") in STARTABLE and doc.get("expires_at", 0) <= time.time():
        raise HTTPException(409, "This proposal expired. Send a new request; nothing was started.")
    project = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id}, {"_id": 0},
    )
    if not project:
        raise HTTPException(403, "This project is no longer accessible.")
    if not doc.get("repo_scope") or doc["repo_scope"] != project_scope(project):
        raise HTTPException(409, "The saved repository/branch scope is missing or changed. Request a new proposal.")
    from services.interaction_contract import read_only_request
    if read_only_request(doc.get("prompt", "")):
        raise HTTPException(409, "This saved request prohibits code changes. Clarify the scope instead of executing it.")
    return doc


async def claim_proposal(db, proposal):
    """Atomic at-most-once execution claim; never reset on an uncertain result."""
    claimed = await db[COLLECTION].find_one_and_update(
        {k: proposal[k] for k in ("id", "user_id", "session_id", "project_id")}
        | {"status": {"$in": list(STARTABLE)}, "expires_at": {"$gt": time.time()}},
        {"$set": {"status": "executing", "execution_request_id": uuid.uuid4().hex,
                  "started_at": time.time()}},
        projection={"_id": 0}, return_document=ReturnDocument.AFTER,
    )
    return claimed


async def receipt_for(db, proposal):
    """Read worker evidence, not prose. Also reconciles a disconnected response."""
    task_id = proposal.get("task_id")
    task = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": proposal["user_id"], "project_id": proposal["project_id"]},
        {"_id": 0, "status": 1, "commit_sha": 1, "commit_full_sha": 1,
         "verified": 1, "push_confirmed": 1,
         "push_failed": 1, "verify_failed": 1, "error": 1},
    ) if task_id else None
    status, sha, error = proposal["status"], None, proposal.get("execution_error")
    if task and task.get("status") == "done":
        commit_sha = task.get("commit_full_sha") or task.get("commit_sha") or ""
        if (_SHA.fullmatch(commit_sha) and (task.get("verified") is True or task.get("push_confirmed") is True)
                and not task.get("push_failed") and not task.get("verify_failed")):
            status, sha, error = "succeeded", commit_sha, None
        else:
            status, error = "failed", "The worker did not provide a verified pushed-commit receipt."
    elif task and task.get("status") in ("failed", "cancelled", "aborted", "rolled_back"):
        status, error = "failed", "The approved task did not complete successfully. Check its task log."
    elif status == "succeeded":
        # A missing/deleted/contradictory task record is not success evidence.
        status, error = "unknown", "The saved task receipt could not be verified."
    receipt = ScopeReceipt(
        proposal_id=proposal["id"], status=status, mode=proposal.get("would_be_mode") or "C",
        task_id=task_id, commit_sha=sha, request_id=proposal.get("execution_request_id"), error=error,
    ).model_dump()
    if status != proposal["status"]:
        await db[COLLECTION].update_one(
            {"id": proposal["id"], "user_id": proposal["user_id"], "status": proposal["status"]},
            {"$set": {"status": status, "execution_receipt": receipt, "updated_at": time.time()}},
        )
    return receipt