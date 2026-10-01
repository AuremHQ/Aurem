"""
services/permission_map_parse.py — parsing half of the Permission Map:
turns Supabase migrations + client code into a plain schema model
(tables, RLS, policies, views, functions, buckets, client usage).
Deterministic (S12); no database connection.
"""
from __future__ import annotations

import re
from typing import Iterator

from services.checkup_common import _files, _is_client, _line_of

_PUBLIC_NAMES = ("product", "post", "article", "blog", "categor", "tag", "faq", "plan",
                 "pricing", "testimonial", "page", "event", "course", "listing", "menu",
                 "feature", "changelog", "country", "city")
_SENSITIVE_NAMES = ("order", "user", "profile", "payment", "invoice", "message", "customer",
                    "account", "subscription", "billing", "booking", "patient", "address",
                    "member", "wallet", "transaction", "ticket", "contact", "lead", "session",
                    "token", "secret", "key", "credential", "audit", "log")
_SENSITIVE_COLS = ("email", "phone", "password", "token", "address", "ssn", "card",
                   "secret", "dob", "salary", "api_key", "iban")
_NON_COLUMN_WORDS = ("primary", "foreign", "unique", "constraint", "check")

_NAME = r'(?:"?public"?\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?'
_POLICY_NAME = r"(\"[^\"]*\"|\w+)"
_CREATE_TABLE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?" + _NAME + r"\s*\(", re.I)
_RLS = re.compile(r"alter\s+table\s+(?:only\s+)?" + _NAME + r"\s+(enable|disable)\s+row\s+level\s+security", re.I)
_POLICY = re.compile(r"create\s+policy\s+" + _POLICY_NAME + r"\s+on\s+" + _NAME + r"([^;]*);", re.I | re.S)
_DROP_POLICY = re.compile(r"drop\s+policy\s+(?:if\s+exists\s+)?" + _POLICY_NAME + r"\s+on\s+" + _NAME, re.I)
_VIEW = re.compile(r"create\s+(?:or\s+replace\s+)?view\s+" + _NAME + r"([^;]*);", re.I | re.S)
_FUNC = re.compile(r"create\s+(?:or\s+replace\s+)?function\s+" + _NAME + r"\s*\(([^;]*?)\$\$.*?\$\$([^;]*);", re.I | re.S)
_BUCKET = re.compile(r"storage\.buckets[^;]*?values\s*\(\s*'([^']+)'\s*,\s*'?[^,)]*'?\s*,\s*(true|false)", re.I | re.S)
_FROM_CALL = re.compile(r"""\.from\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\)\s*\.\s*(select|insert|update|delete|upsert)\b""")

SqlFiles = list[tuple[str, str]]


def friendly(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def classify(name: str, cols: list[str]) -> str:
    n = name.lower()
    if any(k in n for k in _SENSITIVE_NAMES) or any(c in " ".join(cols).lower() for c in _SENSITIVE_COLS):
        return "sensitive"
    return "public" if any(k in n for k in _PUBLIC_NAMES) else "unknown"


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas outside nested parentheses."""
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == ")" and depth == 0:
            break
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def parse_columns(body: str) -> list[str]:
    out = []
    for part in _split_top_level(body):
        words = part.strip().split()
        if words and words[0].lower() not in _NON_COLUMN_WORDS:
            out.append(words[0].strip('"'))
    return out


def _search(pattern: str, tail: str, flags: int = re.I, default: str = "") -> str:
    m = re.search(pattern, tail, flags)
    return m.group(1) if m else default


def parse_policy(tail: str) -> dict:
    roles_raw = _search(r"\bto\s+([A-Za-z_,\s]+?)(?:\s+using|\s+with\s+check|$)", tail, default="public")
    return {
        "cmd": _search(r"\bfor\s+(all|select|insert|update|delete)\b", tail, default="all").lower(),
        "roles": [r.strip().lower() for r in roles_raw.split(",")],
        "using": _search(r"\busing\s*\((.*?)\)\s*(?:with\s+check|$)", tail, re.I | re.S).strip(),
        "check": _search(r"\bwith\s+check\s*\((.*)\)\s*$", tail.strip(), re.I | re.S).strip(),
    }


def is_open(expr: str) -> bool:
    return expr.replace(" ", "").lower() in ("true", "(true)")


def _table_events(text: str) -> list[tuple[int, str, re.Match]]:
    events = [(m.start(), "rls", m) for m in _RLS.finditer(text)]
    events += [(m.start(), "policy", m) for m in _POLICY.finditer(text)]
    events += [(m.start(), "drop", m) for m in _DROP_POLICY.finditer(text)]
    return sorted(events, key=lambda e: e[0])


def _apply_event(tables: dict[str, dict], kind: str, m: re.Match, path: str, text: str) -> None:
    if kind == "rls":
        t = tables.get(m.group(1).lower())
        if t:
            t["rls"] = m.group(2).lower() == "enable"
        return
    t = tables.get(m.group(2).lower())
    if not t:
        return
    name = m.group(1).strip('"')
    if kind == "policy":
        t["policies"].append({**parse_policy(m.group(3)), "name": name,
                              "file": path, "line": _line_of(text, m.start())})
    else:
        t["policies"] = [p for p in t["policies"] if p["name"] != name]


def parse_tables(sql_files: SqlFiles) -> dict[str, dict]:
    tables: dict[str, dict] = {}
    for path, text in sql_files:
        for m in _CREATE_TABLE.finditer(text):
            name = m.group(1).lower()
            cols = parse_columns(text[m.end():])
            tables[name] = {"name": name, "file": path, "line": _line_of(text, m.start()),
                            "columns": cols, "rls": False, "policies": [],
                            "class": classify(name, cols)}
    # Apply RLS/policy statements in migration order (files sorted by path,
    # statements by position) so a later `drop policy` really removes it.
    for path, text in sorted(sql_files):
        for _, kind, m in _table_events(text):
            _apply_event(tables, kind, m, path, text)
    return tables


def apply_answers(tables: dict[str, dict], answers: dict[str, str]) -> None:
    """User answers (public/private) override the guessed class."""
    for t in tables.values():
        a = answers.get(t["name"])
        if a in ("public", "private"):
            t["class"] = "sensitive" if a == "private" else "public"
            t["answered"] = True


def find_views(sql_files: SqlFiles) -> Iterator[tuple[str, int, str, str]]:
    """(path, line, view name, definition tail)."""
    for path, text in sql_files:
        for m in _VIEW.finditer(text):
            yield path, _line_of(text, m.start()), m.group(1), m.group(2)


def find_functions(sql_files: SqlFiles) -> Iterator[tuple[str, int, str, str]]:
    """(path, line, function name, full statement)."""
    for path, text in sql_files:
        for m in _FUNC.finditer(text):
            yield path, _line_of(text, m.start()), m.group(1), m.group(0)


def find_buckets(sql_files: SqlFiles) -> Iterator[tuple[str, int, str, bool]]:
    """(path, line, bucket name, is_public)."""
    for path, text in sql_files:
        for m in _BUCKET.finditer(text):
            yield path, _line_of(text, m.start()), m.group(1), m.group(2).lower() == "true"


def client_usage(tc: dict[str, str]) -> dict[str, set[str]]:
    usage: dict[str, set[str]] = {}
    for path, text in _files(tc):
        if path.endswith((".js", ".jsx", ".ts", ".tsx")) and _is_client(path):
            for m in _FROM_CALL.finditer(text):
                usage.setdefault(m.group(1).lower(), set()).add(m.group(2))
    return usage
