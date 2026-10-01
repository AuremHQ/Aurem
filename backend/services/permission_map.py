"""
services/permission_map.py — "who can see/change what" from the code
alone (Supabase migrations + client usage).  Deterministic (S12); live
database inspection is a separate, consented feature (S5, Batch 3).

Only Supabase-style repos are supported.  Any other stack returns
`supported: False` with a plain reason (S9) — never a guessed map.
Parsing: `permission_map_parse`; findings/questions: `permission_map_items`.
"""
from __future__ import annotations

from typing import Optional

from services.checkup_common import _files, detect_stack
from services.permission_map_items import (  # noqa: F401  (MAX_QUESTIONS re-exported)
    MAX_QUESTIONS, attach_questions, bucket_items, build_questions, function_items,
    table_items, view_items,
)
from services.permission_map_parse import (
    apply_answers, client_usage, friendly, is_open, parse_tables,
)

_FULL = "read and change"


def _merge_mode(mode: str, p: dict) -> str:
    scope = _FULL if p["cmd"] in ("all", "update", "insert", "delete") else "read"
    if is_open(p["using"]) or is_open(p["check"]):
        return scope if (mode == "none" or scope == _FULL) else mode
    return "own rows only" if mode == "none" else mode


def _access(t: dict, roles: tuple[str, ...]) -> str:
    if not t["rls"]:
        return _FULL
    mode = "none"
    for p in t["policies"]:
        if any(r in roles for r in p["roles"]):
            mode = _merge_mode(mode, p)
    return mode


def _matrix(tables: dict[str, dict], usage: dict[str, set[str]]) -> list[dict]:
    return [{
        "name": friendly(t["name"]), "kind": t["class"],
        "visitors": _access(t, ("anon", "public")),
        "signed_in": _access(t, ("authenticated", "anon", "public")),
        "developer": {"table": t["name"], "rls": t["rls"], "policies": t["policies"],
                      "used_by_app": sorted(usage.get(t["name"], []))},
    } for t in sorted(tables.values(), key=lambda x: x["name"])]


def _unsupported() -> dict:
    return {"supported": False,
            "reason": "The data access map is only available for Supabase projects right now.",
            "tables": [], "items": [], "questions": [], "matrix": []}


def build_permission_map(tc: dict[str, str], answers: Optional[dict[str, str]] = None) -> dict:
    if not detect_stack(tc)["supabase"]:
        return _unsupported()
    sql_files = [(p, t) for p, t in _files(tc) if p.endswith(".sql")]
    tables = parse_tables(sql_files)
    apply_answers(tables, {k.lower(): v for k, v in (answers or {}).items()})
    items = (table_items(tables) + view_items(tables, sql_files)
             + function_items(sql_files) + bucket_items(sql_files))
    usage = client_usage(tc)
    questions = build_questions(tables, items, usage)
    attach_questions(items, questions)
    return {
        "supported": True,
        "tables": [{"name": t["name"], "class": t["class"], "rls": t["rls"],
                    "columns": t["columns"], "file": t["file"]} for t in tables.values()],
        "items": items, "questions": questions, "matrix": _matrix(tables, usage),
    }
