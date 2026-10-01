"""
services/checkup_runner.py — drives one checkup run as an SSE stream.

Read the repo (with a timeout) -> run the checks in a worker thread ->
stream stage progress -> persist items + record funnel events.  Every
failure becomes a friendly `error` event ("Your chat still works", S11):
a checkup must never take the chat down with it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import AsyncIterator, Awaitable, Callable, Optional

from services import fix_items as fi
from services.checkup_orchestrator import iter_checkup, seo_cards_to_items

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 45.0
_CHAT_OK = "Your chat still works."


def sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


def _error(message: str) -> dict:
    return {"type": "error", "message": message}


async def _read_code(read_cache: Callable[[], Awaitable[dict]], project_id: str) -> tuple[Optional[dict], Optional[str]]:
    try:
        tc = await asyncio.wait_for(read_cache(), FETCH_TIMEOUT_S)
    except Exception as e:                              # noqa: BLE001
        logger.warning("[checkup] read failed project=%s: %r", project_id, e)
        return None, f"We couldn't read your code right now. {_CHAT_OK}"
    if not tc:
        return None, "We couldn't find any code to check in this project."
    return tc, None


async def _checkup_events(tc: dict, answers: dict, project_id: str) -> AsyncIterator[dict]:
    """Run `iter_checkup` in a thread, yielding its events as they arrive."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def work():
        try:
            for ev in iter_checkup(tc, answers=answers):
                loop.call_soon_threadsafe(q.put_nowait, ev)
        except Exception as e:                          # noqa: BLE001
            logger.warning("[checkup] crashed project=%s: %r", project_id, e)
            loop.call_soon_threadsafe(q.put_nowait, _error(f"The checkup hit a problem. {_CHAT_OK}"))
        loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=work, daemon=True).start()
    while True:
        ev = await q.get()
        if ev is None:
            return
        yield ev


async def _seo_items(db, project_id: str) -> list[dict]:
    seo = await db.first_scan_results.find_one({"project_id": project_id}, {"_id": 0, "cards": 1})
    return [{**x, "id": f"seo::{x['evidence']['file']}:0:seo_page_basics"}
            for x in seo_cards_to_items((seo or {}).get("cards") or [])]


async def _persist(db, *, account_id: str, project_id: str, final: dict) -> None:
    items = list(final["items"]) + await _seo_items(db, project_id)
    for it in items:
        status = await fi.upsert_item(db, user_id=account_id, project_id=project_id, item=it)
        if status == "regressed":
            await fi.record_event(db, user_id=account_id, project_id=project_id, event="item_regressed")
    await fi.save_checkup(db, user_id=account_id, project_id=project_id, items=items,
                          duration_ms=final["duration_ms"], stack=final["stack"]["label"])
    await fi.record_event(db, user_id=account_id, project_id=project_id,
                          event="checkup_verdict_shown", meta={"count": len(items)})
    final["items"] = [fi.to_fix_item({**it, "finding_id": it.get("id"), "project_id": project_id,
                                      "status": "open"}) for it in items]
    final["score"], final["stages"] = fi.score_for(final["items"]), fi.stage_summary(final["items"])


async def run_stream(db, *, account_id: str, project_id: str,
                     read_cache: Callable[[], Awaitable[dict]], answers: dict) -> AsyncIterator[str]:
    await fi.record_event(db, user_id=account_id, project_id=project_id, event="checkup_started")
    yield sse({"type": "reading_code"})
    tc, err = await _read_code(read_cache, project_id)
    if err:
        yield sse(_error(err))
        return
    final = None
    async for ev in _checkup_events(tc, answers, project_id):
        if ev["type"] == "done":
            final = ev
            continue
        yield sse(ev)
        if ev["type"] == "error":
            return
    if final is None:
        return
    try:
        await _persist(db, account_id=account_id, project_id=project_id, final=final)
    except Exception as e:                              # noqa: BLE001
        logger.warning("[checkup] persist failed project=%s: %r", project_id, e)
    yield sse(final)
