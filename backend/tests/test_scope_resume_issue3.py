"""Issue 3 integration: real temporary Mongo + real ASGI/submit path.

MOCKED: authentication resolver, repo credential/context, quotas and task worker.
No real GitHub, LLM, local-repo or production actions are permitted.
"""
import asyncio
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from starlette.requests import Request

from services.scope_confirmation import COLLECTION, create_proposal
from services.scope_resume import project_scope


@pytest_asyncio.fixture
async def env(monkeypatch):
    from routers import cto_projects as tasks
    from routers.chat import router, stream, turn, worker
    from services import chat_helpers, ora_context, session_summary
    from services.ora_chat_v2 import llm_client

    cfg = dotenv_values(Path(__file__).parents[1] / ".env")
    mongo = AsyncIOMotorClient(cfg["MONGO_URL"], serverSelectionTimeoutMS=5000)
    db = mongo[f"scope_resume_test_{uuid.uuid4().hex[:12]}"]
    project = {"project_id": "p1", "user_id": "u1", "github_owner": "fixture",
               "github_repo": "repo", "branch": "test-branch", "installation_id": 123}
    await db.cto_projects.insert_one(project.copy())
    current = {"user_id": "u1", "tier": "founder", "is_unlimited": True}

    async def identity(_auth):
        return dict(current)

    monkeypatch.setattr(stream, "current_dev", identity)
    monkeypatch.setattr(tasks, "current_dev", identity)
    for module in (stream, worker, chat_helpers, tasks):
        monkeypatch.setattr(module, "get_db", lambda: db)
    monkeypatch.setattr(tasks, "require_db", lambda: db)
    monkeypatch.setattr(llm_client, "is_mock", lambda: False)
    monkeypatch.setattr(turn, "_fire_task_sent_bg", AsyncMock())
    monkeypatch.setattr(session_summary, "maybe_update_summary", AsyncMock())
    monkeypatch.setattr(tasks, "assert_has_budget", AsyncMock())
    monkeypatch.setattr(tasks, "assert_has_task_budget", AsyncMock())
    monkeypatch.setattr(ora_context, "build_ora_context", AsyncMock(return_value=SimpleNamespace(pat="MOCKED")))
    mode_route = AsyncMock(side_effect=AssertionError("A saved approval must not be reclassified"))
    monkeypatch.setattr(worker, "_mode_broadcast", mode_route)

    async def commit_worker(task_id, proj, task, files, context, token, maxx):
        assert proj["project_id"] == "p1" and proj["branch"] == "test-branch"
        assert task == 'Add a comment "scope test" to src/example.py'
        await db.cto_tasks.update_one({"task_id": task_id}, {"$set": {
            "status": "done", "commit_sha": "a" * 40, "verified": True,
        }})

    executor = AsyncMock(side_effect=commit_worker)
    monkeypatch.setattr(tasks, "_run_task", executor)
    app = FastAPI()
    app.include_router(router, prefix="/api/aurem-dev")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            yield SimpleNamespace(db=db, client=client, project=project, current=current,
                                  executor=executor, mode_route=mode_route, tasks=tasks)
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


async def proposal(env, **changes):
    data = {"session_id": "s1", "user_id": "u1", "project_id": "p1",
            "prompt": 'Add a comment "scope test" to src/example.py',
            "confidence": 0.3, "threshold": 0.55, "would_be_mode": "C", "repo_scope": project_scope(env.project)}
    data.update(changes)
    return await create_proposal(env.db, **data)


def frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


async def confirm(env, prop=None, **overrides):
    body = {"prompt": "Confirm and proceed", "session_id": "s1", "project_id": "p1"}
    if prop:
        body["scope_confirmed_proposal_id"] = prop["id"]
    body.update(overrides)
    return await env.client.post("/api/aurem-dev/chat/stream", headers={"Authorization": "Bearer MOCKED"}, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("button", [True, False])
async def test_both_transports_dispatch_saved_task_and_return_commit_receipt(env, button):
    p = await proposal(env)
    response = await confirm(env, p if button else None)
    assert response.status_code == 200, response.text
    events = frames(response)
    done = next(e for e in events if e.get("done"))
    receipt = done["scope_execution"]
    assert receipt["status"] == "succeeded"
    assert receipt["commit_sha"] == "a" * 40
    assert done["mode"] == "C" and done["tier"] == "agentic"
    task = await env.db.cto_tasks.find_one({"task_id": receipt["task_id"]}, {"_id": 0})
    assert task["scope_proposal_id"] == p["id"] and task["session_id"] == "s1"
    env.executor.assert_awaited_once()
    env.mode_route.assert_not_awaited()
    history = await env.db.chat_sessions.find_one({"session_id": "s1"})
    assert history["turns"][-1]["scope_execution"]["commit_sha"] == "a" * 40
    again = await confirm(env, p)
    assert frames(again)[-1]["scope_execution"]["task_id"] == receipt["task_id"]
    env.executor.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_button_and_typed_confirmation_claim_once(env):
    p = await proposal(env)
    responses = await asyncio.gather(confirm(env, p), confirm(env))
    assert all(r.status_code == 200 for r in responses)
    assert await env.db.cto_tasks.count_documents({"scope_proposal_id": p["id"]}) == 1
    env.executor.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expired", "cancelled", "wrong_session", "wrong_project", "wrong_user", "changed_prompt", "changed_branch", "readonly"])
async def test_invalid_or_changed_scope_never_dispatches(env, kind):
    p = await proposal(env, **({"prompt": "Read-only: inspect src/example.py; do not change code"} if kind == "readonly" else {}))
    overrides = {}
    if kind == "expired":
        await env.db[COLLECTION].update_one({"id": p["id"]}, {"$set": {"expires_at": time.time() - 1}})
    elif kind == "cancelled":
        await env.db[COLLECTION].update_one({"id": p["id"]}, {"$set": {"status": "cancelled"}})
    elif kind == "wrong_session": overrides["session_id"] = "other"
    elif kind == "wrong_project": overrides["project_id"] = "other"
    elif kind == "wrong_user": env.current["user_id"] = "u2"
    elif kind == "changed_prompt": overrides["prompt"] = "Delete src/example.py"
    elif kind == "changed_branch":
        await env.db.cto_projects.update_one({"project_id": "p1"}, {"$set": {"branch": "different"}})
    response = await confirm(env, p, **overrides)
    assert response.status_code in (403, 404, 409), response.text
    assert await env.db.cto_tasks.count_documents({}) == 0
    env.executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_pending_proposals_require_explicit_selection(env):
    await proposal(env)
    await proposal(env)
    response = await confirm(env)
    assert response.status_code == 409
    env.executor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["missing_commit", "worker_error", "quota", "push_failed"])
async def test_failure_never_reports_a_success_or_reexecutes(env, outcome):
    p = await proposal(env)
    if outcome == "quota":
        env.tasks.assert_has_task_budget.side_effect = HTTPException(402, "Quota reached")
    elif outcome == "worker_error":
        env.executor.side_effect = RuntimeError("simulated worker interruption")
    else:
        async def invalid_receipt(task_id, *_args):
            await env.db.cto_tasks.update_one({"task_id": task_id}, {"$set": {
                "status": "done", "verified": True,
                "commit_sha": "a" * 40 if outcome == "push_failed" else None,
                "push_failed": outcome == "push_failed",
            }})
        env.executor.side_effect = invalid_receipt
    response = await confirm(env, p)
    done = frames(response)[-1]
    assert done["scope_execution"]["status"] in ("failed", "unknown")
    assert not done["scope_execution"]["commit_sha"]
    assert done["turn_state"] != "success"
    count = env.executor.await_count
    await confirm(env, p)
    assert env.executor.await_count == count


@pytest.mark.asyncio
async def test_proposal_event_passes_watchdog_and_history_roundtrip(env):
    from routers.chat.stream_state import StreamState
    from routers.chat.turn import ChatBody
    from routers.chat.watchdog import consume_worker_queue
    from routers.chat.worker import _scope_confirmation_gate
    from services.chat_helpers import _persist_turn

    body = ChatBody(prompt='Add a comment "scope test" to src/example.py', session_id="s1", project_id="p1")
    state = StreamState(request=Request({"type": "http"}), body=body, authorization="Bearer MOCKED",
                        user=env.current, user_id="u1", jwt_token="MOCKED", pid_stream="p1")
    state.mode_confidence = {"needs_confirm": True, "confidence": 0.3}
    result = await _scope_confirmation_gate(state, "C")
    await state.q.put({"type": "result", "result": result})
    dummy = asyncio.create_task(asyncio.sleep(0))
    events = [f async for f in consume_worker_queue(state, dummy, dummy)]
    await dummy
    assert any('"type": "scope_confirmation"' in f for f in events)
    assert result["scope_confirmation_proposal_id"]
    await _persist_turn("u1", "s1", body.prompt, result["content"], result["provider"], project_id="p1",
                        scope_confirmation={"proposal_id": result["scope_confirmation_proposal_id"], "awaiting": True, "prompt": body.prompt})
    doc = await env.db.chat_sessions.find_one({"session_id": "s1"})
    assert doc["turns"][-1]["awaiting_scope_confirmation"] is True
    assert doc["turns"][-1]["scope_confirmation_prompt"] == body.prompt


@pytest.mark.asyncio
async def test_deadline_marks_unknown_and_never_starts_a_second_task(env, monkeypatch):
    from routers.chat import scope_resume
    monkeypatch.setattr(scope_resume, "EXECUTION_DEADLINE_S", 0.25)
    monkeypatch.setattr(scope_resume, "PROGRESS_INTERVAL_S", 0.01)
    async def slow(*_args):
        await asyncio.sleep(10)
    env.executor.side_effect = slow
    p = await proposal(env)
    response = await confirm(env, p)
    assert frames(response)[-1]["scope_execution"]["status"] == "unknown"
    await confirm(env, p)
    env.executor.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnecting_a_claim_does_not_make_it_reexecutable(env):
    from routers.chat.scope_resume import scope_resume_stream
    from routers.chat.turn import ChatBody
    p = await proposal(env)
    response = await scope_resume_stream(
        request=Request({"type": "http"}), db=env.db, user=env.current, authorization="Bearer MOCKED",
        body=ChatBody(prompt="Confirm and proceed", session_id="s1", project_id="p1", scope_confirmed_proposal_id=p["id"]),
    )
    await anext(response.body_iterator)
    await response.body_iterator.aclose()
    doc = await env.db[COLLECTION].find_one({"id": p["id"]})
    assert doc["status"] == "unknown"
    await confirm(env, p)
    env.executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_task_can_reconcile_a_late_push_without_rerunning(env):
    from services.scope_resume import receipt_for
    p = await proposal(env)
    p.update(status="unknown", task_id="t-late")
    await env.db[COLLECTION].update_one({"id": p["id"]}, {"$set": {"status": "unknown", "task_id": "t-late"}})
    await env.db.cto_tasks.insert_one({"task_id": "t-late", "user_id": "u1", "project_id": "p1",
                                     "status": "done", "commit_full_sha": "f" * 40, "push_confirmed": True})
    receipt = await receipt_for(env.db, p)
    assert receipt["status"] == "succeeded" and receipt["commit_sha"] == "f" * 40
    env.executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_ledger_preserves_proposal_controls(env):
    from routers.chat.stream import _replay_retry_gen
    from routers.chat.turn import ChatBody
    from services.chat_reliability import record_completed
    await env.db.chat_request_ledger.insert_one({"request_id": "request1"})
    fields = {"awaiting_scope_confirmation": True, "scope_confirmation_proposal_id": "proposal1",
              "scope_confirmation_prompt": "original", "scope_confirmation_summary": "scope"}
    await record_completed(env.db, request_id="request1", content="scope", provider="scope-confirmation",
                           tool_calls_run=0, turn_state="blocked", scope_confirmation=fields)
    rec = await env.db.chat_request_ledger.find_one({"request_id": "request1"}, {"_id": 0})
    events = [json.loads(f[6:]) async for f in _replay_retry_gen(rec, ChatBody(prompt="original", session_id="s1"))]
    assert events[-1]["scope_confirmation_proposal_id"] == "proposal1"
    assert events[-1]["awaiting_scope_confirmation"] is True


def test_http_body_cannot_forge_internal_proposal_authorization():
    from routers.cto_projects.tasks import TaskBody
    assert TaskBody.model_validate({"project_id": "p1", "task": "x", "_scope_proposal": {"id": "forged"}})._scope_proposal is None