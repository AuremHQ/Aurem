"""
services/security_extra_detect_routes.py — Phase 3 detectors that reason about a
route handler or a small window: RLS / ownership, open redirect, audit logging.

Each detector is `(path, lines, text) -> Iterator[(line_no, snippet)]` and is
registered in services/security_extra_rules.py.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator

from services.security_extra_common import (
    JS,
    JS_ROUTE,
    PY,
    enclosing_def,
    ignored,
    js_block,
    line_ok,
    py_block,
)

# ── RLS / ownership ───────────────────────────────────────────────────
_SQL_CREATE = re.compile(
    r"^\s*create\s+(?!temp|temporary|unlogged\s+table\s+temp)(?:unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?"
    r"(?:\"?(\w+)\"?\.)?\"?(\w+)\"?", re.IGNORECASE)
_SQL_RLS_ENABLED = re.compile(
    r"alter\s+table\s+(?:only\s+)?(?:if\s+exists\s+)?(?:\"?\w+\"?\.)?\"?(\w+)\"?\s+(?:force|enable)\s+row\s+level\s+security",
    re.IGNORECASE)
_SQL_INTERNAL = {"auth", "storage", "pg_catalog", "information_schema", "extensions", "realtime", "vault",
                 "cron", "graphql", "graphql_public", "net", "pgsodium", "supabase_functions", "supabase_migrations",
                 "pgbouncer", "_realtime", "_analytics"}
_OWNER_SIGNAL = re.compile(
    r"user|owner|tenant|account|org_?id|workspace|auth|admin|session|token|depends|permission|role|scope|jwt|"
    r"login|api_key|secret|signature|webhook|public|require|verify|guard|allowed|csrf|current", re.IGNORECASE)
_PY_ROUTE_ID = re.compile(r"""^\s*@\w+\.(?:get|put|patch|delete)\(\s*f?["']([^"']*)["']""")
_PY_LOOKUP = re.compile(r"\b(?:find_one|find_one_and_\w+|update_one|delete_one|get_by_id|find_by_id)\s*\(")
_JS_LOOKUP = re.compile(r"\b(?:findById\w*|findByPk|findUnique|findOne\w*|updateOne|deleteOne)\s*\(")
_PATH_ID_PARAM = re.compile(r"\{(\w*[iI][dD]|id)\}")
_JS_ID_PARAM = re.compile(r":\w*[iI]d\b")


def _sql_unprotected(ln: str, enabled: set[str]) -> bool:
    m = _SQL_CREATE.match(ln)
    if not m:
        return False
    schema, table = (m.group(1) or "public").lower(), m.group(2).lower()
    return schema not in _SQL_INTERNAL and table not in enabled


def _sql_findings(lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    enabled = {m.group(1).lower() for m in _SQL_RLS_ENABLED.finditer(text)}
    for i, ln in enumerate(lines, start=1):
        if line_ok(ln) and _sql_unprotected(ln, enabled):
            yield i, ln


def _first_unowned_lookup(lines: list[str], a: int, b: int, lookup: re.Pattern, uses_param: Callable[[str], bool]) -> int | None:
    """Line index of the first by-id lookup in [a, b), or None when it is suppressed/absent."""
    for k in range(a, b):
        if lookup.search(lines[k]) and uses_param(lines[k]):
            return None if ignored(lines[k]) else k
    return None


def _py_route_finding(lines: list[str], i: int) -> int | None:
    m = _PY_ROUTE_ID.match(lines[i])
    params = _PATH_ID_PARAM.findall(m.group(1)) if m else []
    if not params:
        return None
    a, b = py_block(lines, i)
    if _OWNER_SIGNAL.search("\n".join(lines[a:b])):
        return None
    uses = lambda ln: any(re.search(rf"\b{re.escape(p)}\b", ln) for p in params)
    return _first_unowned_lookup(lines, a, b, _PY_LOOKUP, uses)


def _js_route_finding(lines: list[str], i: int) -> int | None:
    m = JS_ROUTE.search(lines[i])
    if not m or m.group(1) == "post" or not _JS_ID_PARAM.search(m.group(2)):
        return None
    a, b = js_block(lines, i)
    if _OWNER_SIGNAL.search("\n".join(lines[a:b])):
        return None
    return _first_unowned_lookup(lines, a, b, _JS_LOOKUP, lambda ln: "req.params" in ln)


def detect_rls_ownership(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    low = path.lower()
    if low.endswith(".sql"):
        yield from _sql_findings(lines, text)
        return
    finder = _py_route_finding if low.endswith(PY) else _js_route_finding if low.endswith(JS) else None
    if finder is None or (finder is _py_route_finding and "@" not in text):
        return
    for i in range(len(lines)):
        k = finder(lines, i)
        if k is not None:
            yield k + 1, lines[k]


# ── open redirect ─────────────────────────────────────────────────────
_REDIRECT_VARS = {"next", "redirect", "redirect_uri", "redirect_url", "return_to", "return_url", "returnto", "returnurl",
                  "url", "target", "dest", "destination", "callback", "continue_url", "goto", "to", "back"}
_PY_REDIRECT = re.compile(r"""RedirectResponse\(\s*(?:url\s*=\s*)?([A-Za-z_][\w]*)\s*[,)]""")
_REDIRECT_OK = re.compile(
    r"startswith\(|urlparse|urlsplit|_validate_redirect|validate_redirect|is_safe|safe_url|safe_redirect|allowlist|"
    r"allowed|ALLOWED|netloc|same_origin|url_has_allowed_host|\bin\s*[\(\[\{]|\.origin\b|new URL\(", re.IGNORECASE)
_EXPRESS_REDIRECT = re.compile(r"""\bres\.redirect\(\s*(?:\d+\s*,\s*)?req\.(?:query|body|params)\b""")
_JS_LOC_ASSIGN = re.compile(r"""^\s*(?:window\.)?location(?:\.href)?\s*=\s*([A-Za-z_]\w*)\s*;?\s*$|"""
                            r"""\b(?:window\.)?location\.(?:replace|assign)\(\s*([A-Za-z_]\w*)\s*\)""")
_JS_FROM_QUERY = r"(?:URLSearchParams|searchParams\.get|query\.get|params\.get|location\.search|req\.query)"
_JS_SAFE = re.compile(r"startsWith\(|new URL\(|\.origin\b|allow|includes\(|safe", re.IGNORECASE)
_FROM_REQUEST = r"=.*(?:query_params|\.args\b|GET\.get|params\.get)"


def _is_user_param(lines: list[str], def_idx: int, i: int, name: str) -> bool:
    """`name` is a parameter of the enclosing function, or is read from the request in it."""
    sig_end = next((k for k in range(def_idx, min(i + 1, def_idx + 12)) if lines[k].rstrip().endswith(":")), def_idx)
    if re.search(rf"\b{name}\b\s*[:=,)]", " ".join(lines[def_idx:sig_end + 1])):
        return True
    return any(re.search(rf"\b{name}\b\s*{_FROM_REQUEST}", lines[k]) for k in range(def_idx, i + 1))


def _py_redirect_hit(lines: list[str], i: int) -> bool:
    if not line_ok(lines[i]):
        return False
    m = _PY_REDIRECT.search(lines[i])
    if not m or m.group(1) not in _REDIRECT_VARS:
        return False
    def_idx = enclosing_def(lines, i)
    if def_idx is None or not _is_user_param(lines, def_idx, i, m.group(1)):
        return False
    return not _REDIRECT_OK.search("\n".join(lines[def_idx:i + 1]))


def _js_redirect_hit(lines: list[str], i: int) -> bool:
    ln = lines[i]
    if not line_ok(ln):
        return False
    if _EXPRESS_REDIRECT.search(ln):
        return True
    m = _JS_LOC_ASSIGN.search(ln)
    if not m:
        return False
    name = m.group(1) or m.group(2)
    window = "\n".join(lines[max(0, i - 20):i + 1])
    from_query = re.search(rf"\b{re.escape(name)}\b\s*=[^=].*{_JS_FROM_QUERY}", window)
    return bool(from_query) and not _JS_SAFE.search(window)


def detect_open_redirect(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    low = path.lower()
    if low.endswith(PY) and "RedirectResponse" in text:
        hit = _py_redirect_hit
    elif low.endswith(JS):
        hit = _js_redirect_hit
    else:
        return
    for i in range(len(lines)):
        if hit(lines, i):
            yield i + 1, lines[i]


# ── audit logging ─────────────────────────────────────────────────────
_SENSITIVE_PATH = re.compile(
    r"login|signin|sign-in|logout|register|signup|sign-up|password|reset|role|permission|grant|revoke|impersonat", re.IGNORECASE)
_PY_ROUTE_WRITE = re.compile(r"""^\s*@\w+\.(?:post|put|patch|delete)\(\s*f?["']([^"']*)["']""")
_LOG_SIGNAL = re.compile(
    r"logger|logging|\blog(?:_\w+)?\(|audit|record_|track_|print\(|emit|activity|security_event|journal|console\.|"
    r"winston|pino|AuditLog", re.IGNORECASE)


def _py_audit_hit(lines: list[str], i: int) -> bool:
    m = _PY_ROUTE_WRITE.match(lines[i])
    if not m or not _SENSITIVE_PATH.search(m.group(1)) or ignored(lines[i]):
        return False
    a, b = py_block(lines, i)
    return not _LOG_SIGNAL.search("\n".join(lines[a:b]))


def _js_audit_hit(lines: list[str], i: int) -> bool:
    m = JS_ROUTE.search(lines[i])
    if not m or m.group(1) == "get" or not _SENSITIVE_PATH.search(m.group(2)) or ignored(lines[i]):
        return False
    a, b = js_block(lines, i)
    return not _LOG_SIGNAL.search("\n".join(lines[a:b]))


def detect_audit_logging(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    low = path.lower()
    if low.endswith(PY) and "@" in text:
        hit = _py_audit_hit
    elif low.endswith(JS):
        hit = _js_audit_hit
    else:
        return
    for i in range(len(lines)):
        if hit(lines, i):
            yield i + 1, lines[i]
