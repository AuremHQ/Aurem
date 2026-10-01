"""
services/permission_map_items.py — turns the parsed schema into plain-
English checkup items and the (at most 3) questions we ask the user.
"""
from __future__ import annotations

import re
from typing import Optional

from services.checkup_common import _item
from services.permission_map_parse import (
    SqlFiles, find_buckets, find_functions, find_views, friendly, is_open,
)

MAX_QUESTIONS = 3
_SENSITIVE_BUCKETS = ("invoice", "receipt", "document", "private", "kyc", "passport",
                      "contract", "report", "backup", "export")
_WRITE_CMDS = ("insert", "update", "delete", "all")


def _mk(table: str, policy: Optional[str] = None, **kw) -> dict:
    it = _item(**kw)
    it["evidence"]["table"] = table
    if policy:
        it["evidence"]["policy"] = policy
    return it


def _sev(t: dict, hi: str, lo: str) -> str:
    return hi if t["class"] == "sensitive" else lo


def rls_item(t: dict) -> dict:
    fn = friendly(t["name"]).lower()
    return _mk(
        t["name"], scanner="database", rule_id="rls_disabled",
        severity=_sev(t, "critical", "high"),
        title=f"Anyone on the internet can read and change your {fn} data",
        impact="The lock that limits who can touch this list is switched off.",
        file=t["file"], line=t["line"],
        detail=f"table {t['name']}: row level security not enabled",
        confidence="high" if t["class"] != "unknown" else "medium",
        fix_hint="Turn the lock on and add rules for who may read or change rows.")


def _policy_reach(p: dict) -> tuple[bool, bool]:
    """(open to visitors, open to signed-in users)."""
    return any(r in ("anon", "public") for r in p["roles"]), "authenticated" in p["roles"]


def _write_item(t: dict, p: dict, who: str, sev: str) -> dict:
    return _mk(
        t["name"], policy=p["name"], scanner="database", rule_id="policy_open_write",
        severity=sev, title=f"{who} can change your {friendly(t['name']).lower()} data",
        impact="Rows could be altered or deleted by people who don't own them.",
        file=p["file"], line=p["line"],
        detail=f"policy on {t['name']} allows {p['cmd']} with an always-true rule for {', '.join(p['roles'])}",
        fix_hint="Limit the rule to the row's owner.")


def _read_item(t: dict, p: dict, who: str, sev: str) -> dict:
    return _mk(
        t["name"], policy=p["name"], scanner="database", rule_id="policy_open_read",
        severity=sev, title=f"{who} can see everyone's {friendly(t['name']).lower()}",
        impact="Private details could be read by people they weren't meant for.",
        file=p["file"], line=p["line"],
        detail=f"policy on {t['name']} allows select with an always-true rule for {', '.join(p['roles'])}",
        confidence="high" if t["class"] == "sensitive" else "medium",
        fix_hint="Limit the rule to the row's owner, or mark this list as public.")


def _who_sev(t: dict, wide: bool) -> tuple[str, str]:
    if wide:
        return "Any visitor", _sev(t, "critical", "high")
    return "Any signed-in user", _sev(t, "high", "medium")


def _is_open_write(p: dict) -> bool:
    return p["cmd"] in _WRITE_CMDS and (is_open(p["using"]) or is_open(p["check"]))


def _is_open_read(t: dict, p: dict) -> bool:
    return p["cmd"] in ("select", "all") and is_open(p["using"]) and t["class"] != "public"


def _policy_item(t: dict, p: dict) -> Optional[dict]:
    wide, authed = _policy_reach(p)
    if not (wide or authed):
        return None
    who, sev = _who_sev(t, wide)
    if _is_open_write(p):
        return _write_item(t, p, who, sev)
    return _read_item(t, p, who, sev) if _is_open_read(t, p) else None


def table_items(tables: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    for t in tables.values():
        if not t["rls"]:
            out.append(rls_item(t))
            continue
        out.extend(i for i in (_policy_item(t, p) for p in t["policies"]) if i)
    return out


def view_items(tables: dict[str, dict], sql_files: SqlFiles) -> list[dict]:
    sensitive = {n for n, t in tables.items() if t["class"] == "sensitive"}
    out = []
    for path, line, name, tail in find_views(sql_files):
        if re.search(r"security_invoker\s*=\s*(true|on)", tail, re.I):
            continue
        used = [s for s in sensitive if re.search(r"\b" + re.escape(s) + r"\b", tail, re.I)]
        if used:
            out.append(_mk(
                used[0], scanner="database", rule_id="view_bypasses_rls", severity="high",
                title=f"A shortcut view exposes your {friendly(used[0]).lower()} data",
                impact="Views skip the lock on the data underneath unless told otherwise.",
                file=path, line=line,
                detail=f"view {name} reads {used[0]} without security_invoker",
                confidence="medium", fix_hint="Make the view respect the caller's permissions."))
    return out


def function_items(sql_files: SqlFiles) -> list[dict]:
    out = []
    for path, line, name, stmt in find_functions(sql_files):
        if re.search(r"security\s+definer", stmt, re.I) and not re.search(r"set\s+search_path", stmt, re.I):
            out.append(_item(
                scanner="database", rule_id="definer_function", severity="medium",
                title="A database helper runs with more power than the person calling it",
                impact="If it's callable by visitors it could be tricked into showing or changing data.",
                file=path, line=line,
                detail=f"function {name} is SECURITY DEFINER without a fixed search_path",
                confidence="medium"))
    return out


def bucket_items(sql_files: SqlFiles) -> list[dict]:
    out = []
    for path, line, name, public in find_buckets(sql_files):
        if public and any(k in name.lower() for k in _SENSITIVE_BUCKETS):
            out.append(_item(
                scanner="database", rule_id="public_bucket_sensitive", severity="high",
                title=f"Your '{name}' file storage is open to the whole internet",
                impact="Anyone with a link can download these files.",
                file=path, line=line, detail=f"storage bucket {name} is public", confidence="medium"))
    return out


def build_questions(tables: dict[str, dict], items: list[dict],
                    usage: dict[str, set[str]]) -> list[dict]:
    flagged = {i["evidence"].get("table") for i in items}
    questions = []
    for t in sorted(tables.values(), key=lambda x: x["name"]):
        asked = t["class"] == "unknown" and not t.get("answered")
        if asked and (t["name"] in usage or t["name"] in flagged):
            questions.append({
                "table": t["name"],
                "text": f"Should anyone on the internet be able to see your {friendly(t['name']).lower()} list?",
                "options": [{"value": "public", "label": "Yes, it's public"},
                            {"value": "private", "label": "No, keep it private"}],
            })
    return questions[:MAX_QUESTIONS]


def attach_questions(items: list[dict], questions: list[dict]) -> None:
    by_table = {q["table"]: q for q in questions}
    for it in items:
        q = by_table.get(it["evidence"].get("table"))
        if q:
            it["question"] = q
