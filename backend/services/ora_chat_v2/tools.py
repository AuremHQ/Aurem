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
        "description": "True count + per-error-code breakdown of failed fix-loops in the window (newest 20 shown when truncated).",
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

# Part XIV (a1/a2/a3) — shared `days` validation. The three windowed
# tools used to do `int(days or 7)` with no type or bounds check:
# days=10**30 overflowed timedelta in get_alerts, silently widened
# get_recent_loop_failures to an all-time window (float path), and
# flowed unvalidated into compute_journey_watch_card (same timedelta
# overflow downstream); days=-5 queried the future (empty result),
# days=0 silently became 7 while the return echoed 0, days="abc"
# raised ValueError, and days=7.9 silently truncated to 7. Reject
# instead of clamping: a silently narrowed window would lie to the
# model just as badly as a widened one. Max 365 (a full year) is a
# plausible admin window; anything larger is a mistake, not a query.
_MAX_DAYS = 365


def _validated_days(days, default: int = 7) -> tuple:
    """Validate a `days` window arg. Returns (value, None) on success,
    or (None, error_dict) with {"error": "invalid_days", ...} — the
    caller returns the error dict as the tool result (never raises,
    so execute_tool surfaces it instead of an OverflowError)."""
    if days is None:
        return default, None
    if isinstance(days, bool):
        return None, {"error": "invalid_days",
                      "detail": "days must be an integer number of days, not a boolean"}
    if isinstance(days, float):
        if not days.is_integer():
            return None, {"error": "invalid_days",
                          "detail": f"days must be a whole number of days, got {days!r}"}
        days = int(days)
    elif isinstance(days, str):
        s = days.strip()
        if not s.lstrip("-").isdigit():
            return None, {"error": "invalid_days",
                          "detail": f"days must be an integer number of days, got {days!r}"}
        days = int(s)
    elif not isinstance(days, int):
        return None, {"error": "invalid_days",
                      "detail": f"days must be an integer number of days, got {type(days).__name__}"}
    if days < 1:
        return None, {"error": "invalid_days",
                      "detail": f"days must be >= 1, got {days}"}
    if days > _MAX_DAYS:
        return None, {"error": "invalid_days",
                      "detail": f"days must be <= {_MAX_DAYS}, got {days}"}
    return days, None


async def get_funnel_stats(db, days: int = 7) -> dict:
    days, err = _validated_days(days)
    if err is not None:
        return err
    from services.journey_watch import compute_journey_watch_card
    return await compute_journey_watch_card(db, period_days=days)


async def get_alerts(db, days: int = 7) -> dict:
    days, err = _validated_days(days)
    if err is not None:
        return err
    since = datetime.now(timezone.utc) - timedelta(days=days)
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
    # Part XIV (c2) — the 30-item cap was silent. Report the true
    # total (count_documents) so the model knows items were cut.
    total = await db.ora_backlog_items.count_documents({})
    cur = db.ora_backlog_items.find(
        {}, {"_id": 0, "backlog_id": 1, "title": 1, "status": 1, "note": 1,
             "updated_at": 1},
    ).sort("updated_at", -1).limit(30)
    items = [row async for row in cur]
    return {"items": items, "total": total,
            "truncated": total > len(items)}


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
    # Part XIV (b1) — trailing separator: without it, a sibling dir
    # such as /app/memory2/... satisfies the prefix check. The name
    # allowlist above currently blocks every escape input, so this is
    # defence-in-depth — one future refactor of the name rules must
    # not silently re-open the traversal.
    if not real.startswith(os.path.realpath(_INVESTIGATION_DIR) + os.sep):
        return {"error": "invalid_name"}
    if not os.path.isfile(real):
        return {"error": "not_found", "name": name}
    # Part XIV (b2) — the old f.read(20000) silently dropped the tail
    # with no signal. Read the first 20,000 chars, then count (not
    # store) the rest so total_chars is exact without holding a huge
    # file in memory, and flag the cut.
    with open(real, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(20000)
        total_chars = len(content)
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            total_chars += len(chunk)
    truncated = total_chars > len(content)
    return {"name": name, "content": content,
            "truncated": truncated, "total_chars": total_chars}


async def get_recent_loop_failures(db, days: int = 7) -> dict:
    days, err = _validated_days(days)
    if err is not None:
        return err
    since = time.time() - days * 86400
    flt = {"state": "failed", "updated_at": {"$gte": since}}
    # Part XIV (c3) — `count` used to be len(rows): the capped count
    # presented as the window total. Report the TRUE window total via
    # count_documents; the 20-row cap still bounds the payload.
    total = await db.loop_sessions.count_documents(flt)
    cur = db.loop_sessions.find(
        flt,
        {"_id": 0, "error_code": 1, "updated_at": 1, "loop_id": 1},
    ).sort("updated_at", -1).limit(20)
    rows = [r async for r in cur]
    # Part XIV (a4) — the old return read only rows[0]'s error_code,
    # so a model asking "what kinds of failures are happening?" got
    # one class and no hint others existed. Count every code in the
    # shown rows. When truncated, by_error_code covers only the newest
    # 20 (flagged by by_error_code_truncated) — the true total is in
    # `count`/`total`.
    by_error_code: dict[str, int] = {}
    for r in rows:
        code = r.get("error_code") or "unknown"
        by_error_code[code] = by_error_code.get(code, 0) + 1
    truncated = total > len(rows)
    return {"days": days, "count": total, "total": total,
            "shown": len(rows), "truncated": truncated,
            "by_error_code": by_error_code,
            "by_error_code_truncated": truncated,
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
    # Part XIV (c5) — find_files used to silently slice at 25.
    # Forward the total/truncated flags so the model knows matches
    # were cut instead of assuming the list is complete.
    return {"pattern": pattern,
            **await _codebase.find_files(pattern, limit=25,
                                         include_total=True)}


async def read_source_file(db, path: str) -> dict:
    return await _codebase.read_file(path, max_lines=200)


async def search_code_defs(db, name: str) -> dict:
    # Part XIV (c7) — search_defs used to silently slice at 15.
    return {"name": name,
            **await _codebase.search_defs(name, limit=15,
                                          include_total=True)}


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
