"""
services/security_extra_rules.py — seven extra static-scan rules (Phase 3)

Wired into services.security_text_scanner._scan_text, so the 1-click scan,
the loop-engine diff scan and anything else that calls it get them.
The eighth rule of this batch, slop_squat_unknown_package, needs the network
and lives in services/slop_squat_check.py (deep scan only).

  prompt_injection_user_input    HIGH    request data interpolated into an LLM
                                         prompt without delimiting, or raw LLM
                                         output fed to eval/exec/shell
  missing_rls_or_ownership_check HIGH    SQL table without row level security,
                                         or a by-id route that reads/writes a
                                         record with no owner/auth signal
  mass_assignment_request_body   HIGH    whole request body written to the DB /
                                         spread into a model, or a privilege
                                         field copied straight from the body
  open_redirect_user_param       MEDIUM  redirect to a user-supplied URL with no
                                         same-site / allowlist check
  missing_audit_logging          MEDIUM  login / password / role-change route
                                         that logs nothing
  sri_missing_cdn_resource       MEDIUM  third-party <script>/<link stylesheet>
                                         without integrity=
  public_bucket_acl              HIGH    public-read ACL / public bucket policy

These are heuristics: every detector looks at a small window, has explicit
negative cases, honours the `vanguard: ignore` / `security-scan: ignore`
line markers, and the repo's own source is scanned in
tests/test_regression_iter2026_65_new_scan_rules.py to keep false positives
at zero.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

EXTRA_RULE_META: dict[str, dict[str, str]] = {
    "prompt_injection_user_input": {
        "vuln": "prompt_injection", "severity": "high",
        "desc": "User-controlled text is placed in an LLM prompt without delimiting, or raw LLM output is executed",
        "fix_hint": "Wrap user text in delimiters (<user_input>…</user_input>), tell the model to treat it as data, "
                    "and never pass raw model output to eval/exec/shell.",
        "trigger": "request/user text f-string'd or concatenated into an LLM prompt with no delimiter, or LLM output passed to eval/exec/os.system",
    },
    "missing_rls_or_ownership_check": {
        "vuln": "access_control", "severity": "high",
        "desc": "Table without row level security, or a by-id route that touches a record with no owner/auth check",
        "fix_hint": "Enable RLS and add a policy, or filter the lookup by the authenticated user's id (IDOR).",
        "trigger": "SQL CREATE TABLE without ENABLE ROW LEVEL SECURITY, or GET/PUT/DELETE /x/{id} that reads by id without an owner/user filter",
    },
    "mass_assignment_request_body": {
        "vuln": "mass_assignment", "severity": "high",
        "desc": "Whole request body is written to the database / spread into a model, or a privilege field comes from the body",
        "fix_hint": "Copy an explicit allowlist of fields; set role/is_admin/plan server-side only.",
        "trigger": "`$set: body`, `Model(**request.json)`, `Model.create(req.body)`, `{...req.body}`, or `is_admin/role` taken from the body",
    },
    "open_redirect_user_param": {
        "vuln": "open_redirect", "severity": "medium",
        "desc": "Redirect to a user-supplied URL without a same-site or allowlist check",
        "fix_hint": "Only redirect to relative paths (start with one '/', not '//') or to an exact-match allowlist.",
        "trigger": "RedirectResponse/res.redirect/window.location set from `next`/`redirect`/`url` params with no startswith('/') or allowlist check",
    },
    "missing_audit_logging": {
        "vuln": "logging", "severity": "medium",
        "desc": "Login / password / role-change route writes no log or audit record",
        "fix_hint": "Log who did what (user id, action, outcome, IP) for auth and permission changes; never log secrets.",
        "trigger": "login/register/password/role/permission route handlers with no logger/audit call",
    },
    "sri_missing_cdn_resource": {
        "vuln": "supply_chain", "severity": "medium",
        "desc": "Third-party script/stylesheet loaded without a Subresource Integrity hash",
        "fix_hint": 'Add integrity="sha384-…" and crossorigin="anonymous", or self-host the file.',
        "trigger": "<script src>/<link rel=stylesheet> from a CDN host without integrity= (dynamic vendor scripts excepted)",
    },
    "public_bucket_acl": {
        "vuln": "misconfiguration", "severity": "high",
        "desc": "Storage bucket or object is made world-readable/writable",
        "fix_hint": "Use private ACLs, keep block-public-access on, and serve files through signed URLs.",
        "trigger": "ACL public-read, block_public_acls=false, Principal \"*\", allUsers, createBucket public:true, `allow read: if true`",
    },
    "slop_squat_unknown_package": {
        "vuln": "supply_chain", "severity": "high",
        "desc": "Dependency name does not exist on npm/PyPI (AI-invented names can be registered by an attacker)",
        "fix_hint": "Replace it with the real package; verify every AI-suggested dependency on the registry.",
        "trigger": "package.json / requirements.txt dependency that returns 404 on npm/PyPI (deep scan only)",
    },
}

_IGNORE_MARKERS = ("vanguard: ignore", "security-scan: ignore")
_MAX_LINE = 2000
_MAX_PER_RULE = 25
_MAX_TEXT = 2_000_000

_PY = (".py",)
_JS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_HTML = (".html", ".htm")


def _finding(rid: str, path: str, line: int, snippet: str) -> dict:
    meta = EXTRA_RULE_META[rid]
    return {
        "rule_id": rid, "vuln": meta["vuln"], "severity": meta["severity"],
        "file": path, "line": line, "snippet": snippet.strip()[:200],
        "desc": meta["desc"], "message": meta["desc"], "fix_hint": meta["fix_hint"],
    }


def _ignored(line: str) -> bool:
    return any(m in line for m in _IGNORE_MARKERS)


# ── shared route/window helpers ───────────────────────────────────────
def _py_block(lines: list[str], start: int) -> tuple[int, int]:
    """[start, end) of the function that follows the decorator at `start`."""
    n, j, seen_def = len(lines), start + 1, False
    while j < n and j < start + 150:
        ln = lines[j]
        if not ln.strip() or ln.lstrip().startswith("#"):
            j += 1
            continue
        if not seen_def:
            if re.match(r"\s*(?:async\s+)?def\s", ln):
                seen_def = True
            j += 1
            continue
        if ln[0] not in " \t)]}":
            break
        j += 1
    return start, j


_JS_ROUTE = re.compile(r"""\b(?:app|router|server)\.(get|post|put|patch|delete)\(\s*[\"'`]([^\"'`]*)[\"'`]""")


def _js_block(lines: list[str], start: int) -> tuple[int, int]:
    n, j = len(lines), start + 1
    while j < n and j < start + 150:
        ln = lines[j]
        if _JS_ROUTE.search(ln):
            break
        if re.match(r"^\}\)?;?\s*$", ln):
            j += 1
            break
        j += 1
    return start, j


# ── 1. prompt injection ───────────────────────────────────────────────
_LLM_CALL = re.compile(
    r"chat\.completions\.create|\.messages\.create|generate_content|ChatCompletion\.create|"
    r"litellm\.a?completion|LlmChat\s*\(|\.responses\.create|ChatAnthropic|ChatOpenAI|generateText\(|"
    r"send_message\(\s*UserMessage"
)
_USER_PY = (r"(?:body|payload|req_body|request_body|user_input|user_message|user_text|"
            r"request\.json\(\)|request\.query_params)(?:\.\w+|\[[^\]]+\]|\.get\([^)]*\))")
_PROMPT_TARGET = re.compile(r"""(?:prompt|instruction|message|content|query|question|system)\w*["']?\s*[:=]""", re.IGNORECASE)
_PY_FSTR_USER = re.compile(r"""\bf(?:\"\"\"|'''|"|')[^\n]*\{\s*""" + _USER_PY)
_PY_FMT_USER = re.compile(r"""["']\s*\+\s*""" + _USER_PY + r"|\.format\([^)]*" + _USER_PY)
_JS_TPL_USER = re.compile(r"""`[^`\n]*\$\{\s*(?:req\.(?:body|query|params)|body|userInput|user_input)\b[^}]*\}""")
_PI_SAFE = re.compile(
    r"<\w+>[^<\n]*\{[^}\n]*\}[^<\n]*</\w+>|<\w+>[^<\n]*\$\{[^}\n]*\}[^<\n]*</\w+>|```|json\.dumps|JSON\.stringify|"
    r"sanitiz|escape|neutraliz|delimit|truncate", re.IGNORECASE)
_EXEC_LLM = re.compile(
    r"\b(?:eval|exec)\s*\(|os\.system\s*\(|subprocess\.\w+\s*\(|new\s+Function\s*\(|child_process")
_LLM_OUTPUT = re.compile(
    r"\.choices\[0\]|\.message\.content|\.content\[0\]\.text|llm_output|llm_response|model_output|completion\.text")


def _detect_prompt_injection(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    if not path.lower().endswith(_PY + _JS) or not _LLM_CALL.search(text):
        return
    for i, ln in enumerate(lines, start=1):
        if len(ln) > _MAX_LINE or _ignored(ln):
            continue
        if _EXEC_LLM.search(ln) and _LLM_OUTPUT.search(ln):
            yield i, ln
            continue
        if not _PROMPT_TARGET.search(ln) or _PI_SAFE.search(ln):
            continue
        if _PY_FSTR_USER.search(ln) or _PY_FMT_USER.search(ln) or _JS_TPL_USER.search(ln):
            yield i, ln


# ── 2. RLS / ownership ────────────────────────────────────────────────
_SQL_CREATE = re.compile(
    r"^\s*create\s+(?!temp|temporary|unlogged\s+table\s+temp)(?:unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?"
    r"(?:\"?(\w+)\"?\.)?\"?(\w+)\"?", re.IGNORECASE)
_SQL_INTERNAL = {"auth", "storage", "pg_catalog", "information_schema", "extensions", "realtime", "vault",
                 "cron", "graphql", "graphql_public", "net", "pgsodium", "supabase_functions", "supabase_migrations",
                 "pgbouncer", "_realtime", "_analytics"}
_OWNER_SIGNAL = re.compile(
    r"user|owner|tenant|account|org_?id|workspace|auth|admin|session|token|depends|permission|role|scope|jwt|"
    r"login|api_key|secret|signature|webhook|public|require|verify|guard|allowed|csrf|current", re.IGNORECASE)
_PY_ROUTE_ID = re.compile(r"""^\s*@\w+\.(?:get|put|patch|delete)\(\s*f?["']([^"']*)["']""")
_PY_LOOKUP = re.compile(r"\b(?:find_one|find_one_and_\w+|update_one|delete_one|get_by_id|find_by_id)\s*\(")
_JS_LOOKUP = re.compile(r"\b(?:findById\w*|findByPk|findUnique|findOne\w*|updateOne|deleteOne)\s*\(")


def _detect_rls_ownership(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    low = path.lower()
    if low.endswith(".sql"):
        enabled = {m.group(1).lower() for m in re.finditer(
            r"alter\s+table\s+(?:only\s+)?(?:if\s+exists\s+)?(?:\"?\w+\"?\.)?\"?(\w+)\"?\s+(?:force|enable)\s+row\s+level\s+security",
            text, re.IGNORECASE)}
        for i, ln in enumerate(lines, start=1):
            if len(ln) > _MAX_LINE or _ignored(ln):
                continue
            m = _SQL_CREATE.match(ln)
            if not m:
                continue
            schema, table = (m.group(1) or "public").lower(), m.group(2).lower()
            if schema in _SQL_INTERNAL or table in enabled:
                continue
            yield i, ln
        return
    if low.endswith(_PY):
        if "@" not in text:
            return
        for i, ln in enumerate(lines):
            m = _PY_ROUTE_ID.match(ln)
            if not m:
                continue
            params = re.findall(r"\{(\w*[iI][dD]|id)\}", m.group(1))
            if not params:
                continue
            a, b = _py_block(lines, i)
            window = "\n".join(lines[a:b])
            if _OWNER_SIGNAL.search(window):
                continue
            for k in range(a, b):
                if _PY_LOOKUP.search(lines[k]) and any(re.search(rf"\b{re.escape(p)}\b", lines[k]) for p in params):
                    if not _ignored(lines[k]):
                        yield k + 1, lines[k]
                    break
        return
    if low.endswith(_JS):
        for i, ln in enumerate(lines):
            m = _JS_ROUTE.search(ln)
            if not m or m.group(1) == "post" or not re.search(r":\w*[iI]d\b", m.group(2)):
                continue
            a, b = _js_block(lines, i)
            window = "\n".join(lines[a:b])
            if _OWNER_SIGNAL.search(window):
                continue
            for k in range(a, b):
                if _JS_LOOKUP.search(lines[k]) and "req.params" in lines[k]:
                    if not _ignored(lines[k]):
                        yield k + 1, lines[k]
                    break


# ── 3. mass assignment ────────────────────────────────────────────────
_MA_PY = [
    re.compile(r"""["']\$set["']\s*:\s*(?:body|payload|request_body|req_body)(?:\.(?:dict|model_dump)\(\))?\s*[},]"""),
    re.compile(r"""\(\s*\*\*\s*(?:await\s+)?request\.json\b"""),
    re.compile(r"""["'](?:is_admin|isAdmin|is_superuser|is_staff|role|roles|is_verified|email_verified)["']\s*:\s*"""
               r"""(?:body|payload|data|request_body|req_body)\s*(?:\[|\.get\()"""),
]
_MA_JS = [
    re.compile(r"""\.(?:create|update\w*|findByIdAndUpdate|findOneAndUpdate|insertOne|insertMany|updateOne|save|upsert)\(\s*(?:[^,()]+,\s*)?req\.body\s*[,)]"""),
    re.compile(r"""\.\.\.\s*req\.body\b"""),
    re.compile(r"""Object\.assign\(\s*\w+\s*,\s*req\.body\s*\)"""),
]


_ADMIN_GATE = re.compile(r"_?require_admin|admin_only|is_admin\(|_require_founder|require_founder")


def _py_mass_assignment_is_safe(lines: list[str], idx: int, pattern_no: int, line: str) -> bool:
    """Two legitimate shapes that look like mass assignment:
      * `$set: payload` where `payload` is a dict literal built in this function
        (server-chosen fields, not the request body);
      * the handler is admin-gated, so an admin editing a settings document is
        not a privilege escalation by an ordinary caller."""
    lo = max(0, idx - 40)
    def_idx = next((k for k in range(idx, lo - 1, -1) if re.match(r"\s*(?:async\s+)?def\s", lines[k])), None)
    window = "\n".join(lines[def_idx if def_idx is not None else idx:idx + 1])
    if _ADMIN_GATE.search(window):
        return True
    if pattern_no == 0:
        m = re.search(r"""["']\$set["']\s*:\s*(\w+)""", line)
        if m and re.search(rf"\b{re.escape(m.group(1))}\s*=\s*\{{", "\n".join(lines[lo:idx + 1])):
            return True
    return False


def _detect_mass_assignment(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    low = path.lower()
    if low.endswith(_PY):
        pats = _MA_PY
    elif low.endswith(_JS):
        pats = _MA_JS
    else:
        return
    for i, ln in enumerate(lines, start=1):
        if len(ln) > _MAX_LINE or _ignored(ln):
            continue
        hit = next((k for k, p in enumerate(pats) if p.search(ln)), None)
        if hit is None:
            continue
        if low.endswith(_PY) and _py_mass_assignment_is_safe(lines, i - 1, hit, ln):
            continue
        yield i, ln


# ── 4. open redirect ──────────────────────────────────────────────────
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


def _detect_open_redirect(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    low = path.lower()
    if low.endswith(_PY):
        if "RedirectResponse" not in text:
            return
        for i, ln in enumerate(lines):
            if len(ln) > _MAX_LINE or _ignored(ln):
                continue
            m = _PY_REDIRECT.search(ln)
            if not m or m.group(1) not in _REDIRECT_VARS:
                continue
            name = m.group(1)
            lo = max(0, i - 40)
            def_idx = next((k for k in range(i, lo - 1, -1) if re.match(r"\s*(?:async\s+)?def\s", lines[k])), None)
            if def_idx is None:
                continue
            sig_end = next((k for k in range(def_idx, min(i + 1, def_idx + 12))
                            if lines[k].rstrip().endswith(":")), def_idx)
            sig = " ".join(lines[def_idx:sig_end + 1])
            from_query = any(re.search(rf"\b{name}\b\s*=.*(?:query_params|\.args\b|GET\.get|params\.get)", lines[k])
                             for k in range(def_idx, i + 1))
            if not (re.search(rf"\b{name}\b\s*[:=,)]", sig) or from_query):
                continue
            if _REDIRECT_OK.search("\n".join(lines[def_idx:i + 1])):
                continue
            yield i + 1, ln
    elif low.endswith(_JS):
        for i, ln in enumerate(lines):
            if len(ln) > _MAX_LINE or _ignored(ln):
                continue
            if _EXPRESS_REDIRECT.search(ln):
                yield i + 1, ln
                continue
            m = _JS_LOC_ASSIGN.search(ln)
            if not m:
                continue
            name = m.group(1) or m.group(2)
            lo = max(0, i - 20)
            window = "\n".join(lines[lo:i + 1])
            if re.search(rf"\b{re.escape(name)}\b\s*=[^=].*{_JS_FROM_QUERY}", window) and not re.search(
                    r"startsWith\(|new URL\(|\.origin\b|allow|includes\(|safe", window, re.IGNORECASE):
                yield i + 1, ln


# ── 5. audit logging ──────────────────────────────────────────────────
_SENSITIVE_PATH = re.compile(
    r"login|signin|sign-in|logout|register|signup|sign-up|password|reset|role|permission|grant|revoke|impersonat", re.IGNORECASE)
_PY_ROUTE_WRITE = re.compile(r"""^\s*@\w+\.(?:post|put|patch|delete)\(\s*f?["']([^"']*)["']""")
_LOG_SIGNAL = re.compile(
    r"logger|logging|\blog(?:_\w+)?\(|audit|record_|track_|print\(|emit|activity|security_event|journal|console\.|"
    r"winston|pino|AuditLog", re.IGNORECASE)


def _detect_audit_logging(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    low = path.lower()
    if low.endswith(_PY):
        if "@" not in text:
            return
        for i, ln in enumerate(lines):
            m = _PY_ROUTE_WRITE.match(ln)
            if not m or not _SENSITIVE_PATH.search(m.group(1)) or _ignored(ln):
                continue
            a, b = _py_block(lines, i)
            if not _LOG_SIGNAL.search("\n".join(lines[a:b])):
                yield i + 1, ln
    elif low.endswith(_JS):
        for i, ln in enumerate(lines):
            m = _JS_ROUTE.search(ln)
            if not m or m.group(1) == "get" or not _SENSITIVE_PATH.search(m.group(2)) or _ignored(ln):
                continue
            a, b = _js_block(lines, i)
            if not _LOG_SIGNAL.search("\n".join(lines[a:b])):
                yield i + 1, ln


# ── 6. SRI ────────────────────────────────────────────────────────────
_TAG = re.compile(r"<(script|link)\b([^<>]*)>", re.IGNORECASE | re.DOTALL)
_EXEMPT_HOSTS = {
    "fonts.googleapis.com", "fonts.gstatic.com", "use.typekit.net", "p.typekit.net", "www.googletagmanager.com",
    "googletagmanager.com", "www.google-analytics.com", "analytics.google.com", "js.stripe.com", "m.stripe.network",
    "apis.google.com", "accounts.google.com", "www.gstatic.com", "www.google.com", "connect.facebook.net",
    "static.cloudflareinsights.com", "challenges.cloudflare.com", "checkout.razorpay.com", "www.paypal.com",
    "js.paypal.com", "widget.intercom.io", "js.intercomcdn.com", "cdn.segment.com", "localhost", "127.0.0.1", "0.0.0.0",
}
_EXEMPT_SUFFIXES = (".posthog.com", ".emergent.sh", ".stripe.com", ".googleapis.com.invalid")


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf"""\b{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", attrs, re.IGNORECASE)
    return (m.group(1) or m.group(2) or m.group(3)) if m else None


def _detect_sri(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    if not path.lower().endswith(_HTML) or "<" not in text:
        return
    for m in _TAG.finditer(text):
        tag, attrs = m.group(1).lower(), m.group(2)
        if re.search(r"\bintegrity\s*=", attrs, re.IGNORECASE):
            continue
        if tag == "script":
            url = _attr(attrs, "src")
        else:
            rel = _attr(attrs, "rel") or ""
            url = _attr(attrs, "href") if "stylesheet" in rel.lower() else None
        if not url:
            continue
        hm = re.match(r"^(?:https?:)?//([^/:?#\s]+)", url.strip(), re.IGNORECASE)
        if not hm:
            continue
        host = hm.group(1).lower()
        if host in _EXEMPT_HOSTS or host.endswith(_EXEMPT_SUFFIXES):
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        first = lines[line_no - 1] if line_no - 1 < len(lines) else ""
        if _ignored(first):
            continue
        yield line_no, first or m.group(0)


# ── 7. public bucket ──────────────────────────────────────────────────
_PB_LINE = [
    re.compile(r"""\bacl["']?\s*[:=]\s*["']public-read(?:-write)?["']""", re.IGNORECASE),
    re.compile(r"\b(?:block_public_acls|block_public_policy|ignore_public_acls|restrict_public_buckets)\s*=\s*false\b", re.IGNORECASE),
    re.compile(r"""["'](?:BlockPublicAcls|BlockPublicPolicy|IgnorePublicAcls|RestrictPublicBuckets)["']\s*:\s*false""", re.IGNORECASE),
    re.compile(r"""["']Principal["']\s*:\s*(?:["']\*["']|\{\s*["']AWS["']\s*:\s*["']\*["']\s*\})"""),
    re.compile(r"""\bidentifiers\s*=\s*\[\s*["']\*["']\s*\]"""),
    re.compile(r"""createBucket\([^)\n]*\bpublic\s*:\s*true"""),
    re.compile(r"""\b(?:container_access_type|public_access)\s*=\s*["'](?:blob|container)["']"""),
    re.compile(r"""\ballow_(?:blob_public_access|nested_items_to_be_public)\s*=\s*true\b"""),
    re.compile(r"""\ballow\s+[\w\s,]+:\s*if\s+true\s*;"""),
]
_PB_GCS_MEMBER = re.compile(r"""\b(?:member|members|principal)\b[^\n]*["']all(?:Authenticated)?Users["']""")
_PB_GCS_CONTEXT = re.compile(r"storage|bucket|objectViewer|objectAdmin", re.IGNORECASE)
_PB_PREFILTER = re.compile(r"acl|public|Principal|identifiers|allUsers|allAuthenticatedUsers|if\s+true|container_access", re.IGNORECASE)


def _detect_public_bucket(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    if not _PB_PREFILTER.search(text):
        return
    for i, ln in enumerate(lines):
        if len(ln) > _MAX_LINE or _ignored(ln) or "Deny" in ln:
            continue
        if any(p.search(ln) for p in _PB_LINE) or _PB_GCS_MEMBER.search(ln) and _PB_GCS_CONTEXT.search("\n".join(lines[max(0, i - 3):i + 4])):
            yield i + 1, ln


_DETECTORS: list[tuple[str, Callable[[str, list[str], str], Iterator[tuple[int, str]]]]] = [
    ("prompt_injection_user_input", _detect_prompt_injection),
    ("missing_rls_or_ownership_check", _detect_rls_ownership),
    ("mass_assignment_request_body", _detect_mass_assignment),
    ("open_redirect_user_param", _detect_open_redirect),
    ("missing_audit_logging", _detect_audit_logging),
    ("sri_missing_cdn_resource", _detect_sri),
    ("public_bucket_acl", _detect_public_bucket),
]


def scan_extra_rules(path: str, text: str) -> list[dict]:
    """Findings from the seven static rules for one file. Never raises."""
    if not isinstance(text, str) or not text or len(text) > _MAX_TEXT or not isinstance(path, str):
        return []
    lines = text.splitlines()
    out: list[dict] = []
    for rid, detector in _DETECTORS:
        try:
            for count, (line_no, snippet) in enumerate(detector(path, lines, text), start=1):
                out.append(_finding(rid, path, line_no, snippet))
                if count >= _MAX_PER_RULE:
                    break
        except Exception as exc:  # noqa: BLE001 - a heuristic must never break the scan
            logger.debug("extra rule %s failed on %s: %r", rid, path, exc)
    return out
