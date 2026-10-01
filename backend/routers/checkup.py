"""
routers/checkup.py — checkup-first endpoints (PR 4).

Every endpoint is gated by the `checkup_flow` flag (default OFF; S1/S13)
and returns 404 `not_enabled` when off, so merging this changes nothing
for anyone until the flag is turned on by the owner.  The run endpoint
streams stage progress over SSE and NEVER blocks chat (S11): the heavy
work runs in a worker thread, with a wall-clock cap.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from cto_services.auth import current_dev
from cto_services.db import get_db
from services import fix_items as fi
from services.checkup_runner import FETCH_TIMEOUT_S, run_stream
from services.feature_flags import is_enabled
from services.checkup_meta import read_with_meta
from services.repo_text_cache import build_text_cache as _build_text_cache
from services.team_permissions import check_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/checkup", tags=["Checkup"])

_RUN_COOLDOWN_S = 20.0
_last_run: dict[str, float] = {}


async def _ctx(authorization: Optional[str], project_id: Optional[str]):
    user = await current_dev(authorization)
    uid = str(user.get("user_id") or user.get("id") or user.get("email") or "")
    if not await is_enabled("checkup_flow", user_id=uid, tier=user.get("tier")):
        raise HTTPException(404, "not_enabled")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    ctx = await check_permission(user, "projects", "write", db=db)
    account_id = ctx["effective_account_id"]
    if not project_id:
        raise HTTPException(400, "project_id required")
    return user, db, account_id


@router.get("/flags")
async def flags(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    uid = str(user.get("user_id") or user.get("id") or user.get("email") or "")
    return {f: await is_enabled(f, user_id=uid, tier=user.get("tier")) for f in fi.CHECKUP_FLAGS}


@router.get("/latest")
async def latest(project_id: str, authorization: Optional[str] = Header(None)) -> dict:
    _, db, account_id = await _ctx(authorization, project_id)
    last = await db.project_checkups.find_one(
        {"user_id": account_id, "project_id": project_id}, {"_id": 0},
        sort=[("created_at", -1)])
    items = await fi.list_items(db, user_id=account_id, project_id=project_id)
    return {"checkup": last, "items": items,
            "score": fi.score_for(items), "stages": fi.stage_summary(items)}


def _check_cooldown(key: str) -> None:
    now = time.monotonic()
    if now - _last_run.get(key, -1e9) < _RUN_COOLDOWN_S:
        raise HTTPException(429, "A checkup just ran for this project. Give it a few seconds.")
    _last_run[key] = now


async def _project_access(db, account_id: str, project_id: str):
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": account_id},
        {"_id": 0, "github_owner": 1, "github_repo": 1, "github_token": 1,
         "github_branch": 1, "auth_method": 1, "installation_id": 1, "user_id": 1})
    if not proj:
        raise HTTPException(404, "Project not found")
    from services.pat_vault import get_repo_token, GithubAppAuthError
    try:
        pat = await get_repo_token(proj)
    except GithubAppAuthError as e:
        raise HTTPException(400 if e.code in ("app_installation_missing", "app_installation_revoked") else 502, e.detail)
    owner, repo = proj.get("github_owner") or "", proj.get("github_repo") or ""
    if not (owner and repo and pat):
        raise HTTPException(400, "Project missing GitHub linkage")
    return proj, owner, repo, pat


async def _saved_answers(db, account_id: str, project_id: str) -> dict:
    answers = {}
    async for a in db.permission_answers.find({"user_id": account_id, "project_id": project_id}, {"_id": 0}):
        answers[a["table"]] = a["answer"]
    return answers


@router.post("/run")
async def run(body: dict, authorization: Optional[str] = Header(None)):
    project_id = (body or {}).get("project_id")
    user, db, account_id = await _ctx(authorization, project_id)
    _check_cooldown(f"{account_id}:{project_id}")
    _, owner, repo, pat = await _project_access(db, account_id, project_id)
    answers = await _saved_answers(db, account_id, project_id)
    guardian = await is_enabled("guardian_rescan", user_id=account_id, tier=user.get("tier"))
    stream = run_stream(db, account_id=account_id, project_id=project_id, answers=answers,
                        read_cache=lambda: read_with_meta(owner, repo, pat, _build_text_cache), guardian=guardian)
    return StreamingResponse(stream, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/items/ignore")
async def ignore(body: dict, authorization: Optional[str] = Header(None)) -> dict:
    project_id, item_id = (body or {}).get("project_id"), (body or {}).get("item_id")
    _, db, account_id = await _ctx(authorization, project_id)
    ok = await fi.set_status(db, user_id=account_id, project_id=project_id, item_id=item_id or "", new_status="ignored")
    if ok:
        await fi.record_event(db, user_id=account_id, project_id=project_id, event="item_ignored")
    return {"ok": ok}


@router.post("/answer")
async def answer(body: dict, authorization: Optional[str] = Header(None)) -> dict:
    project_id = (body or {}).get("project_id")
    table, value = (body or {}).get("table"), (body or {}).get("answer")
    _, db, account_id = await _ctx(authorization, project_id)
    if value not in ("public", "private") or not isinstance(table, str) or not table:
        raise HTTPException(400, "answer must be 'public' or 'private'")
    await db.permission_answers.update_one(
        {"user_id": account_id, "project_id": project_id, "table": table.lower()},
        {"$set": {"answer": value}}, upsert=True)
    await fi.record_event(db, user_id=account_id, project_id=project_id, event="question_answered")
    return {"ok": True}


@router.post("/skip")
async def skip(body: dict, authorization: Optional[str] = Header(None)) -> dict:
    project_id = (body or {}).get("project_id")
    _, db, account_id = await _ctx(authorization, project_id)
    await fi.record_event(db, user_id=account_id, project_id=project_id, event="checkup_skipped")
    return {"ok": True}


@router.post("/project-memory/preview")
async def project_memory_preview(body: dict, authorization: Optional[str] = Header(None)) -> dict:
    """Draft rules file for the user to read; nothing is written to their repo."""
    project_id = (body or {}).get("project_id")
    _, db, account_id = await _ctx(authorization, project_id)
    _, owner, repo, pat = await _project_access(db, account_id, project_id)
    try:
        tc = await asyncio.wait_for(_build_text_cache(owner, repo, pat), FETCH_TIMEOUT_S)
    except Exception:                                   # noqa: BLE001
        return {"ok": False, "message": "We couldn't read your code right now. Nothing was changed."}
    from services.project_memory import build_project_memory
    return {"ok": True, "committed": False,
            "files": build_project_memory(tc, (body or {}).get("app_purpose") or "")}


async def _fix_ctx(authorization, body):
    project_id = (body or {}).get("project_id")
    user, db, account_id = await _ctx(authorization, project_id)
    if not await is_enabled("fix_loop_v2", user_id=account_id, tier=user.get("tier")):
        raise HTTPException(404, "not_enabled")
    proj, owner, repo, pat = await _project_access(db, account_id, project_id)
    from services.fix_loop import GithubAdapter
    from services.git_identity import resolve_git_identity
    git = GithubAdapter(owner=owner, repo=repo, token=pat,
                        author=await resolve_git_identity(db, account_id))
    return project_id, db, account_id, owner, repo, pat, proj.get("github_branch") or "main", git


@router.post("/items/fix")
async def fix(body: dict, authorization: Optional[str] = Header(None)) -> dict:
    project_id, db, account_id, owner, repo, pat, branch, git = await _fix_ctx(authorization, body)
    try:
        tc = await asyncio.wait_for(_build_text_cache(owner, repo, pat), FETCH_TIMEOUT_S)
    except Exception:                                   # noqa: BLE001
        return {"ok": False, "reason": "read_failed", "message": "We couldn't read your code right now. Nothing was changed."}
    from services.fix_loop import run_fix
    return await run_fix(db, user_id=account_id, project_id=project_id,
                         item_id=(body or {}).get("item_id") or "", tc=tc, base_branch=branch, git=git)


@router.post("/items/undo")
async def undo(body: dict, authorization: Optional[str] = Header(None)) -> dict:
    project_id, db, account_id, *_rest, git = await _fix_ctx(authorization, body)
    from services.fix_loop import undo_fix
    return await undo_fix(db, user_id=account_id, project_id=project_id,
                          item_id=(body or {}).get("item_id") or "", git=git)
