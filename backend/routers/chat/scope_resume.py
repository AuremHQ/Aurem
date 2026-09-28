"""Bounded saved-scope execution on the existing authenticated chat stream.

Uses the SAME task submission checks/worker as Ship. No generic intent re-route,
no model-generated success, and no new GitHub integration/credential path.
"""
import asyncio
import json
import time

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from services.scope_confirmation import COLLECTION
from services.scope_resume import claim_proposal, receipt_for, resolve_proposal
from services.user_response_sanitizer import sanitize_provider_for_user

EXECUTION_DEADLINE_S = 180
PROGRESS_INTERVAL_S = 2

def frame(payload):
    return f"data: {json.dumps(payload)}\n\n"


def receipt_result(receipt):
    status, task_id = receipt["status"], receipt.get("task_id")
    if status == "succeeded":
        content = f"The approved change was committed. Commit: `{receipt['commit_sha']}`. Task: `{task_id}`."
        turn_state = "success"
    elif status in ("executing", "unknown"):
        content = (f"Execution is {status}. No completed commit is being claimed. "
                   + (f"Track task `{task_id}`; do not submit a new approval." if task_id else "Check this proposal's status before retrying."))
        turn_state = "partial"
    else:
        content = receipt.get("error") or "This proposal did not execute. Send a new request after checking the task log."
        turn_state = "error"
    return {"ok": status == "succeeded", "content": content, "turn_state": turn_state,
            "scope_execution": receipt, "scope_confirmation_proposal_id": receipt["proposal_id"],
            "awaiting_scope_confirmation": False, "task_id": task_id,
            "mode": receipt["mode"], "tier": "agentic", "provider": "scope-proposal-executor",
            "continue_available": False,
            **({"error": content} if turn_state == "error" else {})}


async def execute_saved_scope(db, proposal, *, request, authorization):
    from routers.cto_projects.tasks import TaskBody, submit_task

    task_body = TaskBody(project_id=proposal["project_id"], task=proposal["prompt"])
    task_body._scope_proposal = proposal
    background = BackgroundTasks()
    submitted = await submit_task(request, task_body, background, authorization)
    if submitted.get("ok") is not True or not submitted.get("task_id"):
        raise HTTPException(409, submitted.get("message") or "Task dispatch was not accepted.")
    proposal["task_id"] = submitted["task_id"]
    await db[COLLECTION].update_one(
        {"id": proposal["id"], "status": "executing"},
        {"$set": {"task_id": submitted["task_id"]}},
    )
    # Await, don't fire-and-forget. Disconnect/deadline cancels this execution;
    # any in-flight external write remains UNKNOWN, never blindly retried.
    await background()
    receipt = await receipt_for(db, proposal)
    if receipt["status"] == "executing":
        raise RuntimeError("Task worker returned without a terminal execution receipt")
    return receipt


async def scope_resume_stream(*, request, body, user, authorization, db):
    proposal = await resolve_proposal(
        db, user_id=user["user_id"], session_id=body.session_id,
        project_id=body.project_id, prompt=body.prompt,
        proposal_id=body.scope_confirmed_proposal_id,
    )

    async def generate():
        running = None
        claimed = None
        receipt = None
        try:
            claimed = await claim_proposal(db, proposal)
            current = claimed or await db[COLLECTION].find_one(
                {"id": proposal["id"], "user_id": user["user_id"]}, {"_id": 0},
            )
            yield frame({"meta": True, "session_id": body.session_id,
                         "provider": sanitize_provider_for_user("scope-proposal-executor"),
                         "mode": proposal["would_be_mode"]})
            yield frame({"type": "mode", "mode": proposal["would_be_mode"], "needs_confirm": False})
            yield frame({"type": "intent", "intent": {"tier": "agentic", "method": "saved-proposal"}})
            if not claimed:
                receipt = await receipt_for(db, current or proposal)
            else:
                running = asyncio.create_task(execute_saved_scope(
                    db, claimed, request=request, authorization=authorization,
                ))
                started = time.monotonic()
                shown_task = None
                # Strict per-request bound, not a background scheduling loop.
                while not running.done():
                    await asyncio.wait({running}, timeout=PROGRESS_INTERVAL_S)
                    if claimed.get("task_id") and shown_task != claimed["task_id"]:
                        shown_task = claimed["task_id"]
                        yield frame({"type": "task_handoff", "task_id": shown_task,
                                     "project_id": body.project_id, "source": "scope-proposal-executor"})
                    if time.monotonic() - started >= EXECUTION_DEADLINE_S:
                        raise TimeoutError("Execution deadline reached; inspect the task before retrying.")
                    if not running.done():
                        yield frame({"thinking": True, "elapsed_s": round(time.monotonic() - started),
                                     "activity": "Waiting for the approved task's execution receipt"})
                receipt = await running
        except (asyncio.CancelledError, GeneratorExit):
            if claimed:
                await asyncio.shield(db[COLLECTION].update_one(
                    {"id": claimed["id"], "status": "executing"},
                    {"$set": {"status": "unknown", "execution_error": "Response disconnected; inspect the task receipt."}},
                ))
            raise
        except Exception as exc:  # noqa: BLE001 — fail closed with a durable, non-retriable receipt
            if claimed:
                # Once a task exists, an error can follow an external commit.
                # Do not label it safe-to-retry or claim nothing changed.
                status = "unknown" if claimed.get("task_id") else "failed"
                message = str(exc.detail) if isinstance(exc, HTTPException) else "Execution could not be verified. Check the proposal/task record."
                await db[COLLECTION].update_one(
                    {"id": claimed["id"], "status": "executing"},
                    {"$set": {"status": status, "execution_error": message}},
                )
                claimed.update(status=status, execution_error=message)
                receipt = await receipt_for(db, claimed)
            else:
                yield frame({"error": "Confirmation could not be claimed. No new execution was started."})
                return
        finally:
            if running and not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        if receipt:
            result = receipt_result(receipt)
            from services.chat_helpers import _persist_turn
            await _persist_turn(
                user["user_id"], body.session_id, body.prompt, result["content"], result["provider"],
                project_id=body.project_id, shipped_task_id=receipt.get("task_id"),
                scope_confirmation={"proposal_id": receipt["proposal_id"], "execution": receipt},
                interaction={"version": 1, "request_id": receipt.get("request_id"),
                             "session_id": body.session_id, "turn_state": result["turn_state"],
                             "completion_basis": "commit_receipt" if receipt.get("commit_sha") else receipt["status"],
                             "continue_available": False},
            )
            yield frame({"token": result["content"]})
            yield frame({"done": True, "session_id": body.session_id, **result,
                         "provider": sanitize_provider_for_user(result.get("provider"))})

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
    })