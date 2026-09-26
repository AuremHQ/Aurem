"""
services/ora_chat_v2/tools.py — Admin ORA Chat rebuild, P5.

The ONLY read-only tools exposed to the model (function calling).
They double as the READ risk-tier of the P4 action catalog — no
approval needed, they execute instantly. A model attempt to call
anything not in TOOL_SCHEMAS is rejected here and logged.

web_verify / web_inspect (2026-08-30) — the 2 web-inspect tools,
wrapping services.web_inspect (itself a thin wrapper over the
existing V1 verify engine, L17 reuse-first). Target: ANY external
http/https URL, no per-project lock. The SSRF/loopback/sandbox fence
in services.deploy_verify holds even for the admin's own request.

2026-02 · Phase 3.3 — read_source_file / find_source_files /
search_code_defs / get_repo_tree wire the EXISTING read-only
`services/ora_chat/codebase_index` module (already used by the
legacy /slash pipeline) into the actual v2 engine that /message runs
on. Before this, the v2 tool catalog had zero way to look at real
source — every code-related answer was a guess dressed up as
knowledge. `codebase_index` reads straight off `/app/backend` and
`/app/frontend/src` on disk (15-min in-memory TTL, force-refreshed on
every call here via `_ensure_fresh()` inside each helper) — there is
no separate snapshot/copy to fall out of sync; it always reflects
whatever is currently checked out in this pod.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from services.ora_chat import codebase_index as _codebase

logger = logging.getLogger(__name__)

_INVESTIGATION_DIR = "/app/memory"


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_funnel_stats",
        "description": "7-day signup funnel stage counts, stalls, hard-breaks.",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "default": 7}}}}},
    {"type": "function", "function": {
        "name": "get_alerts",
        "description": "Recent admin bell/notification events, counted by kind.",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "default": 7}}}}},
    {"type": "function", "function": {
        "name": "get_backlog",
        "description": "Parked + queued internal backlog items, with status.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_production_status",
        "description": "Current live build hash + health.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_investigation",
        "description": "Read a memory/investigation_*.md file by name.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "get_recent_loop_failures",
        "description": "Count + last error class of failed fix-loops in the window.",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "default": 7}}}}},
    {"type": "function", "function": {
        "name": "web_verify",
        "description": "Deterministic (zero-LLM) deploy-verify engine against ANY external http/https URL — version/TTFB/runtime/breakage/geometry checks + screenshots. Use for 'check if <url> deployed OK' / 'run deploy-verify on <url>' / 'is the pricing page good?'.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "web_inspect",
        "description": "Fetch a pruned snapshot of ANY external http/https URL and get a plain-English, ADVISORY-ONLY answer (via an LLM) to a specific question about it — e.g. 'why is this page showing X?', 'summarize this page', 'does this layout look broken?'. Never proposes fixes or takes actions.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}, "question": {"type": "string"}},
            "required": ["url", "question"]}}},
    {"type": "function", "function": {
        "name": "get_repo_tree",
        "description": "Top-level directory/file listing of the real AUREM repo (backend + frontend/src), grouped by folder. Filenames only, not content — use read_source_file to see what's actually inside one.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "find_source_files",
        "description": "Glob-search real repo-relative file paths, e.g. '*Drawer.jsx' or 'services/ora_chat/*.py'. Use this to locate a file before reading it.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "read_source_file",
        "description": "Read the ACTUAL current content of one real source file by its repo-relative path (e.g. 'backend/routers/ora_chat.py'). This is the only way to ground a claim about what code does or contains — never describe a file's contents without calling this first.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search_code_defs",
        "description": "Find which real file(s) define a function/class name, e.g. 'run_turn' or 'PrivateRoute'.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]}}},
]

_KNOWN_TOOLS = {s["function"]["name"] for s in TOOL_SCHEMAS}


async def get_funnel_stats(db, days: int = 7) -> dict:
    from services.journey_watch import compute_journey_watch_card
    return await compute_journey_watch_card(db, period_days=int(days or 7))


async def get_alerts(db, days: int = 7) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=int(days or 7))
    by_kind: dict[str, int] = {}
    cur = db.health_notifications.find(
        {"created_at_dt": {"$gte": since}}, {"_id": 0, "category": 1, "name": 1})
    rows = 0
    async for row in cur:
        rows += 1
        k = row.get("category") or "other"
        by_kind[k] = by_kind.get(k, 0) + 1
    return {"days": days, "total": rows, "by_kind": by_kind}


async def get_backlog(db) -> dict:
    cur = db.ora_backlog_items.find(
        {}, {"_id": 0, "backlog_id": 1, "title": 1, "status": 1, "note": 1,
             "updated_at": 1},
    ).sort("updated_at", -1).limit(30)
    return {"items": [row async for row in cur]}


async def get_production_status(db) -> dict:
    from services.ora_chat_v2.state_block import _production_status
    return await _production_status(db)


_SAFE_NAME_RE = None


async def read_investigation(db, name: str) -> dict:
    import re
    global _SAFE_NAME_RE
    if _SAFE_NAME_RE is None:
        _SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")
    name = (name or "").strip()
    if not name.startswith("investigation_") or not name.endswith(".md") \
            or not _SAFE_NAME_RE.match(name) or "/" in name or ".." in name:
        return {"error": "invalid_name", "detail": "must be memory/investigation_*.md"}
    path = os.path.join(_INVESTIGATION_DIR, name)
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(_INVESTIGATION_DIR)):
        return {"error": "invalid_name"}
    if not os.path.isfile(real):
        return {"error": "not_found", "name": name}
    with open(real, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(20000)
    return {"name": name, "content": content}


async def get_recent_loop_failures(db, days: int = 7) -> dict:
    since = time.time() - int(days or 7) * 86400
    cur = db.loop_sessions.find(
        {"state": "failed", "updated_at": {"$gte": since}},
        {"_id": 0, "error_code": 1, "updated_at": 1, "loop_id": 1},
    ).sort("updated_at", -1).limit(20)
    rows = [r async for r in cur]
    return {"days": days, "count": len(rows),
            "last_error_class": rows[0].get("error_code") if rows else None}


async def web_verify(db, url: str, user_id: str = "") -> dict:
    from services.web_inspect import run_web_verify
    return await run_web_verify(url, db=db, user_id=user_id)


async def web_inspect(db, url: str, question: str, user_id: str = "") -> dict:
    from services.web_inspect import run_web_inspect
    return await run_web_inspect(url, question, db=db, user_id=user_id)


async def get_repo_tree(db) -> dict:
    return {"tree": await _codebase.compact_tree(max_files=150)}


async def find_source_files(db, pattern: str) -> dict:
    return {"pattern": pattern, "matches": await _codebase.find_files(pattern, limit=25)}


async def read_source_file(db, path: str) -> dict:
    return await _codebase.read_file(path, max_lines=200)


async def search_code_defs(db, name: str) -> dict:
    return {"name": name, "results": await _codebase.search_defs(name, limit=15)}


_DISPATCH = {
    "get_funnel_stats": get_funnel_stats,
    "get_alerts": get_alerts,
    "get_backlog": get_backlog,
    "get_production_status": get_production_status,
    "read_investigation": read_investigation,
    "get_recent_loop_failures": get_recent_loop_failures,
    "web_verify": web_verify,
    "web_inspect": web_inspect,
    "get_repo_tree": get_repo_tree,
    "find_source_files": find_source_files,
    "read_source_file": read_source_file,
    "search_code_defs": search_code_defs,
}
_USER_ID_TOOLS = {"web_verify", "web_inspect"}


async def execute_tool(db, name: str, args: dict, *, user_id: str = "") -> dict:
    """Dispatch a model tool-call. Anything not in TOOL_SCHEMAS is
    rejected + logged — the model cannot call an undefined tool."""
    if name not in _KNOWN_TOOLS:
        logger.warning("ora_chat_v2: rejected undefined tool call: %s", name)
        return {"error": "undefined_tool", "name": name}
    fn = _DISPATCH[name]
    try:
        kwargs = {k: v for k, v in (args or {}).items()
                  if k in ("days", "name", "url", "question", "pattern", "path")}
        if name in _USER_ID_TOOLS:
            kwargs["user_id"] = user_id
        return await fn(db, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("ora_chat_v2 tool %s failed: %r", name, e)
        return {"error": type(e).__name__, "detail": str(e)[:200]}
