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
import json
import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from cto_services.auth import current_dev
from cto_services.db import get_db
from services import fix_items as fi
from services.checkup_orchestrator import iter_checkup, seo_cards_to_items
from services.feature_flags import is_enabled
from services.repo_text_cache import build_text_cache as _build_text_cache
from services.team_permissions import check_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/checkup", tags=["Checkup"])

_RUN_COOLDOWN_S = 20.0
_FETCH_TIMEOUT_S = 45.0
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


@router.post("/run")
async def run(body: dict, authorization: Optional[str] = Header(None)):
    project_id = (body or {}).get("project_id")
    user, db, account_id = await _ctx(authorization, project_id)
    key = f"{account_id}:{project_id}"
    now = time.monotonic()
    if now - _last_run.get(key, -1e9) < _RUN_COOLDOWN_S:
        raise HTTPException(429, "A checkup just ran for this project. Give it a few seconds.")
    _last_run[key] = now

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

    answers = {}
    async for a in db.permission_answers.find({"user_id": account_id, "project_id": project_id}, {"_id": 0}):
        answers[a["table"]] = a["answer"]

    async def gen():
        def sse(ev: dict) -> str:
            return f"data: {json.dumps(ev, default=str)}\n\n"

        await fi.record_event(db, user_id=account_id, project_id=project_id, event="checkup_started")
        yield sse({"type": "reading_code"})
        try:
            tc = await asyncio.wait_for(_build_text_cache(owner, repo, pat), _FETCH_TIMEOUT_S)
        except Exception as e:                          # noqa: BLE001
            logger.warning("[checkup] read failed project=%s: %r", project_id, e)
            yield sse({"type": "error", "message": "We couldn't read your code right now. Your chat still works."})
            return
        if not tc:
            yield sse({"type": "error", "message": "We couldn't find any code to check in this project."})
            return

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def work():
            try:
                for ev in iter_checkup(tc, answers=answers):
                    loop.call_soon_threadsafe(q.put_nowait, ev)
            except Exception as e:                      # noqa: BLE001
                logger.warning("[checkup] crashed project=%s: %r", project_id, e)
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "message": "The checkup hit a problem. Your chat still works."})
            loop.call_soon_threadsafe(q.put_nowait, None)

        threading.Thread(target=work, daemon=True).start()
        final = None
        while True:
            ev = await q.get()
            if ev is None:
                break
            if ev["type"] == "done":
                final = ev
                continue
            yield sse(ev)
            if ev["type"] == "error":
                return
        if final is None:
            return
        try:
            seo = await db.first_scan_results.find_one({"project_id": project_id}, {"_id": 0, "cards": 1})
            items = list(final["items"]) + [
                {**x, "id": f"seo::{x['evidence']['file']}:0:seo_page_basics"}
                for x in seo_cards_to_items((seo or {}).get("cards") or [])]
            for it in items:
                await fi.upsert_item(db, user_id=account_id, project_id=project_id, item=it)
            await fi.save_checkup(db, user_id=account_id, project_id=project_id, items=items,
                                  duration_ms=final["duration_ms"], stack=final["stack"]["label"])
            await fi.record_event(db, user_id=account_id, project_id=project_id,
                                  event="checkup_verdict_shown", meta={"count": len(items)})
            final["items"] = [fi.to_fix_item({**it, "finding_id": it.get("id"), "project_id": project_id,
                                              "status": "open"}) for it in items]
            final["score"], final["stages"] = fi.score_for(final["items"]), fi.stage_summary(final["items"])
        except Exception as e:                          # noqa: BLE001
            logger.warning("[checkup] persist failed project=%s: %r", project_id, e)
        yield sse(final)

    return StreamingResponse(gen(), media_type="text/event-stream",
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
