"""
services/permission_map.py — "who can see/change what" from the code
alone (Supabase migrations + client usage).  Deterministic (S12); live
database inspection is a separate, consented feature (S5, Batch 3).

Only Supabase-style repos are supported.  Any other stack returns
`supported: False` with a plain reason (S9) — never a guessed map.
"""
from __future__ import annotations

import re
from typing import Optional

from services.checkup_checks import _is_client, _files, _item, _line_of, detect_stack

MAX_QUESTIONS = 3

_PUBLIC_NAMES = ("product", "post", "article", "blog", "categor", "tag", "faq", "plan",
                 "pricing", "testimonial", "page", "event", "course", "listing", "menu",
                 "feature", "changelog", "country", "city")
_SENSITIVE_NAMES = ("order", "user", "profile", "payment", "invoice", "message", "customer",
                    "account", "subscription", "billing", "booking", "patient", "address",
                    "member", "wallet", "transaction", "ticket", "contact", "lead", "session",
                    "token", "secret", "key", "credential", "audit", "log")
_SENSITIVE_COLS = ("email", "phone", "password", "token", "address", "ssn", "card",
                   "secret", "dob", "salary", "api_key", "iban")
_SENSITIVE_BUCKETS = ("invoice", "receipt", "document", "private", "kyc", "passport",
                      "contract", "report", "backup", "export")

_NAME = r'(?:"?public"?\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?'
_CREATE_TABLE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?" + _NAME + r"\s*\(", re.I)
_RLS = re.compile(r"alter\s+table\s+(?:only\s+)?" + _NAME + r"\s+(enable|disable)\s+row\s+level\s+security", re.I)
_POLICY = re.compile(r"create\s+policy\s+(\"[^\"]*\"|\w+)\s+on\s+" + _NAME + r"([^;]*);", re.I | re.S)
_DROP_POLICY = re.compile(r"drop\s+policy\s+(?:if\s+exists\s+)?(\"[^\"]*\"|\w+)\s+on\s+" + _NAME, re.I)
_VIEW = re.compile(r"create\s+(?:or\s+replace\s+)?view\s+" + _NAME + r"([^;]*);", re.I | re.S)
_FUNC = re.compile(r"create\s+(?:or\s+replace\s+)?function\s+" + _NAME + r"\s*\(([^;]*?)\$\$.*?\$\$([^;]*);", re.I | re.S)
_BUCKET = re.compile(r"storage\.buckets[^;]*?values\s*\(\s*'([^']+)'\s*,\s*'?[^,)]*'?\s*,\s*(true|false)", re.I | re.S)
_FROM_CALL = re.compile(r"""\.from\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\)\s*\.\s*(select|insert|update|delete|upsert)\b""")


def friendly(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _classify(name: str, cols: list[str]) -> str:
    n = name.lower()
    if any(k in n for k in _SENSITIVE_NAMES) or any(c in " ".join(cols).lower() for c in _SENSITIVE_COLS):
        return "sensitive"
    if any(k in n for k in _PUBLIC_NAMES):
        return "public"
    return "unknown"


def _columns(body: str) -> list[str]:
    cols, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            cols.append(cur)
            cur = ""
        else:
            cur += ch
    cols.append(cur)
    out = []
    for c in cols:
        w = c.strip().split()
        if w and w[0].lower() not in ("primary", "foreign", "unique", "constraint", "check"):
            out.append(w[0].strip('"'))
    return out


def _parse_policy(tail: str) -> dict:
    cmd = (re.search(r"\bfor\s+(all|select|insert|update|delete)\b", tail, re.I) or [None, "all"])[1].lower()
    roles_m = re.search(r"\bto\s+([A-Za-z_,\s]+?)(?:\s+using|\s+with\s+check|$)", tail, re.I)
    roles = [r.strip().lower() for r in roles_m.group(1).split(",")] if roles_m else ["public"]
    using = (re.search(r"\busing\s*\((.*?)\)\s*(?:with\s+check|$)", tail, re.I | re.S) or [None, ""])[1].strip()
    check = (re.search(r"\bwith\s+check\s*\((.*)\)\s*$", tail.strip(), re.I | re.S) or [None, ""])[1].strip()
    return {"cmd": cmd, "roles": roles, "using": using, "check": check}


def _is_open(expr: str) -> bool:
    return expr.replace(" ", "").lower() in ("true", "(true)")


def build_permission_map(tc: dict[str, str], answers: Optional[dict[str, str]] = None) -> dict:
    stack = detect_stack(tc)
    if not stack["supabase"]:
        return {"supported": False,
                "reason": "The data access map is only available for Supabase projects right now.",
                "tables": [], "items": [], "questions": [], "matrix": []}
    answers = {k.lower(): v for k, v in (answers or {}).items()}
    sql_files = [(p, t) for p, t in _files(tc) if p.endswith(".sql")]
    tables: dict[str, dict] = {}
    items: list[dict] = []

    def mk(table, policy=None, **kw):
        it = _item(**kw)
        it["evidence"]["table"] = table
        if policy:
            it["evidence"]["policy"] = policy
        return it

    for path, text in sql_files:
        for m in _CREATE_TABLE.finditer(text):
            name = m.group(1).lower()
            cols = _columns(text[m.end():])
            tables[name] = {"name": name, "file": path, "line": _line_of(text, m.start()),
                            "columns": cols, "rls": False, "policies": [],
                            "class": _classify(name, cols)}
    # Apply RLS/policy statements in migration order (files sorted by path,
    # statements by position) so a later `drop policy` really removes it.
    for path, text in sorted(sql_files):
        events = []
        for m in _RLS.finditer(text):
            events.append((m.start(), "rls", m))
        for m in _POLICY.finditer(text):
            events.append((m.start(), "policy", m))
        for m in _DROP_POLICY.finditer(text):
            events.append((m.start(), "drop", m))
        for _, kind, m in sorted(events, key=lambda e: e[0]):
            if kind == "rls":
                t = tables.get(m.group(1).lower())
                if t:
                    t["rls"] = m.group(2).lower() == "enable"
            elif kind == "policy":
                t = tables.get(m.group(2).lower())
                if t:
                    t["policies"].append({**_parse_policy(m.group(3)), "name": m.group(1).strip('"'),
                                          "file": path, "line": _line_of(text, m.start())})
            else:
                t = tables.get(m.group(2).lower())
                if t:
                    nm = m.group(1).strip('"')
                    t["policies"] = [p for p in t["policies"] if p["name"] != nm]

    # user answers override the guess (public/private)
    for t in tables.values():
        a = answers.get(t["name"])
        if a in ("public", "private"):
            t["class"] = "sensitive" if a == "private" else "public"
            t["answered"] = True

    def sev_for(t, hi, lo):
        return hi if t["class"] == "sensitive" else lo

    for t in tables.values():
        fn = friendly(t["name"])
        if not t["rls"]:
            items.append(mk(t["name"],
                scanner="database", rule_id="rls_disabled",
                severity=sev_for(t, "critical", "high"),
                title=f"Anyone on the internet can read and change your {fn.lower()} data",
                impact="The lock that limits who can touch this list is switched off.",
                file=t["file"], line=t["line"],
                detail=f"table {t['name']}: row level security not enabled",
                confidence="high" if t["class"] != "unknown" else "medium",
                fix_hint="Turn the lock on and add rules for who may read or change rows."))
            continue
        for p in t["policies"]:
            wide = any(r in ("anon", "public") for r in p["roles"])
            authed = "authenticated" in p["roles"]
            reads = p["cmd"] in ("select", "all") and _is_open(p["using"])
            writes = p["cmd"] in ("insert", "update", "delete", "all") and (
                _is_open(p["using"]) or _is_open(p["check"]))
            if writes and (wide or authed):
                who = "Any visitor" if wide else "Any signed-in user"
                items.append(mk(t["name"], policy=p["name"],
                    scanner="database", rule_id="policy_open_write",
                    severity=sev_for(t, "critical", "high") if wide else sev_for(t, "high", "medium"),
                    title=f"{who} can change your {fn.lower()} data",
                    impact="Rows could be altered or deleted by people who don't own them.",
                    file=p["file"], line=p["line"],
                    detail=f"policy on {t['name']} allows {p['cmd']} with an always-true rule for {', '.join(p['roles'])}",
                    fix_hint="Limit the rule to the row's owner."))
            elif reads and t["class"] != "public" and (wide or authed):
                who = "Any visitor" if wide else "Any signed-in user"
                items.append(mk(t["name"], policy=p["name"],
                    scanner="database", rule_id="policy_open_read",
                    severity=sev_for(t, "critical", "high") if wide else sev_for(t, "high", "medium"),
                    title=f"{who} can see everyone's {fn.lower()}",
                    impact="Private details could be read by people they weren't meant for.",
                    file=p["file"], line=p["line"],
                    detail=f"policy on {t['name']} allows select with an always-true rule for {', '.join(p['roles'])}",
                    confidence="high" if t["class"] == "sensitive" else "medium",
                    fix_hint="Limit the rule to the row's owner, or mark this list as public."))

    sensitive = {n for n, t in tables.items() if t["class"] == "sensitive"}
    for path, text in sql_files:
        for m in _VIEW.finditer(text):
            tail = m.group(2)
            if re.search(r"security_invoker\s*=\s*(true|on)", tail, re.I):
                continue
            used = [s for s in sensitive if re.search(r"\b" + re.escape(s) + r"\b", tail, re.I)]
            if used:
                items.append(mk(used[0],
                    scanner="database", rule_id="view_bypasses_rls", severity="high",
                    title=f"A shortcut view exposes your {friendly(used[0]).lower()} data",
                    impact="Views skip the lock on the data underneath unless told otherwise.",
                    file=path, line=_line_of(text, m.start()),
                    detail=f"view {m.group(1)} reads {used[0]} without security_invoker",
                    confidence="medium", fix_hint="Make the view respect the caller's permissions."))
        for m in _FUNC.finditer(text):
            stmt = m.group(0)
            if re.search(r"security\s+definer", stmt, re.I) and not re.search(r"set\s+search_path", stmt, re.I):
                items.append(_item(
                    scanner="database", rule_id="definer_function", severity="medium",
                    title="A database helper runs with more power than the person calling it",
                    impact="If it's callable by visitors it could be tricked into showing or changing data.",
                    file=path, line=_line_of(text, m.start()),
                    detail=f"function {m.group(1)} is SECURITY DEFINER without a fixed search_path",
                    confidence="medium"))
        for m in _BUCKET.finditer(text):
            if m.group(2).lower() == "true" and any(k in m.group(1).lower() for k in _SENSITIVE_BUCKETS):
                items.append(_item(
                    scanner="database", rule_id="public_bucket_sensitive", severity="high",
                    title=f"Your '{m.group(1)}' file storage is open to the whole internet",
                    impact="Anyone with a link can download these files.",
                    file=path, line=_line_of(text, m.start()),
                    detail=f"storage bucket {m.group(1)} is public", confidence="medium"))

    usage: dict[str, set[str]] = {}
    for path, text in _files(tc):
        if path.endswith((".js", ".jsx", ".ts", ".tsx")) and _is_client(path):
            for m in _FROM_CALL.finditer(text):
                usage.setdefault(m.group(1).lower(), set()).add(m.group(2))

    questions = []
    def _item_table(it):
        return it["evidence"].get("table")

    with_items = {_item_table(i) for i in items}
    for t in sorted(tables.values(), key=lambda x: x["name"]):
        if t["class"] == "unknown" and not t.get("answered") and (
                t["name"] in usage or t["name"] in with_items):
            questions.append({
                "table": t["name"],
                "text": f"Should anyone on the internet be able to see your {friendly(t['name']).lower()} list?",
                "options": [{"value": "public", "label": "Yes, it's public"},
                            {"value": "private", "label": "No, keep it private"}],
            })
    questions = questions[:MAX_QUESTIONS]
    for it in items:
        q = next((q for q in questions if q["table"] == _item_table(it)), None)
        if q:
            it["question"] = q

    matrix = []
    for t in sorted(tables.values(), key=lambda x: x["name"]):
        def access(roles_any):
            mode = "none"
            for p in t["policies"]:
                if not t["rls"]:
                    return "read and change"
                if any(r in roles_any for r in p["roles"]):
                    open_ = _is_open(p["using"]) or _is_open(p["check"])
                    scope = "read and change" if p["cmd"] in ("all", "update", "insert", "delete") else "read"
                    if open_:
                        mode = scope if (mode == "none" or scope == "read and change") else mode
                    elif mode == "none":
                        mode = "own rows only"
            return "read and change" if not t["rls"] else mode
        matrix.append({"name": friendly(t["name"]), "kind": t["class"],
                       "visitors": access(("anon", "public")),
                       "signed_in": access(("authenticated", "anon", "public")),
                       "developer": {"table": t["name"], "rls": t["rls"], "policies": t["policies"],
                                     "used_by_app": sorted(usage.get(t["name"], []))}})
    return {"supported": True, "tables": [{"name": t["name"], "class": t["class"], "rls": t["rls"],
                                           "columns": t["columns"], "file": t["file"]}
                                          for t in tables.values()],
            "items": items, "questions": questions, "matrix": matrix}
