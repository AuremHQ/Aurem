"""
services/checkup_checks_logs_cookies.py — Check Library v2, layer 10:
L10.4 sensitive data written to logs, L10.6 insecure session cookies.

Both read call arguments, not just words: `console.log("password reset
sent")` mentions a password in a message and is fine; `console.log(password)`
or `` `${token}` `` writes the value and is not.  Cookies are judged by
name, so a `theme` cookie never triggers (C3).
"""
from __future__ import annotations

import re

from services.checkup_common import _files, _item, _line_of

_CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_SKIP = re.compile(r"(?:^|/)(?:tests?|__tests__|fixtures?|examples?|docs?|scripts?|qa)/|\.(?:test|spec)\.|(?:^|/)test_", re.I)
_MAX_ARGS = 600


def _call_args(text: str, open_idx: int) -> str:
    """Text between the parenthesis at `open_idx` and its match (bounded)."""
    depth = 0
    for i in range(open_idx, min(len(text), open_idx + _MAX_ARGS)):
        c = text[i]
        depth += (c == "(") - (c == ")")
        if depth == 0:
            return text[open_idx + 1:i]
    return text[open_idx + 1: open_idx + _MAX_ARGS]


# ── L10.4 sensitive data in logs ──────────────────────────────────────
_LOG_CALL = re.compile(r"\b(?:console\.(?:log|info|debug|warn|error)|(?:logger|logging|log)\.(?:debug|info|warning|warn|error|exception|critical)|print)\(")
_TEMPLATE_EXPR = re.compile(r"\$\{([^}]*)\}|\{([^{}]*)\}")
_QUOTED = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_]\w*)*")
_SENSITIVE = re.compile(r"^(?:\w*_)?(?:password|passwd|pwd|secret|api_?key|token|jwt|access_?token|refresh_?token|auth_?token|id_?token|"
                        r"authorization|bearer|credit_?card|card_?number|cvv|ssn)(?:_\w*)?$", re.I)
_HARMLESS_PART = re.compile(r"hash|length|len|reset|changed|updated|valid|strength|policy|required|label|error|err|missing|"
                            r"invalid|mismatch|empty|placeholder|field|name|type|count|enabled|next|page|csrf|cursor|expir", re.I)
_REQ_HEADERS = re.compile(r"\b(?:req|request)\.headers\b(?!\[)")
_SAFE_WRAPPER = re.compile(r"\b(?:bool|len|isinstance|type)\([^()]*(?:\([^()]*\)[^()]*)*\)")


def _snake(token: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", token).lower()


def _interpolations(arg: str) -> str:
    """Keep what is *evaluated*: bare code, plus the `${...}` / f-string `{...}` parts of strings."""
    inner = " ".join(a or b for a, b in _TEMPLATE_EXPR.findall(arg))
    code = _QUOTED.sub(" ", arg) + " " + _QUOTED.sub(" ", inner)
    return _SAFE_WRAPPER.sub(" ", code)          # `bool(token)` logs a yes/no, not the token


def _logs_sensitive(args: str) -> bool:
    code = _interpolations(args)
    if _REQ_HEADERS.search(code):
        return True
    for ident in _IDENT.findall(code):
        last = _snake(ident.split(".")[-1])
        if _SENSITIVE.match(last) and not _HARMLESS_PART.search(last.replace("password", "").replace("secret", "")):
            return True
    return False


def check_sensitive_logs(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        if not path.endswith(_CODE_EXT) or _SKIP.search(path):
            continue
        hits = [m for m in _LOG_CALL.finditer(text) if _logs_sensitive(_call_args(text, m.end() - 1))]
        if not hits:
            continue
        out.append(_item(
            scanner="privacy", rule_id="sensitive_data_in_logs", severity="high",
            title="Passwords or login keys are being written into your logs",
            impact="Logs are copied to dashboards, support tools and backups. Anyone who can read them can take over accounts.",
            file=path, line=_line_of(text, hits[0].start()),
            detail=f"{len(hits)} log statement(s) print a password, token or auth header (first at line {_line_of(text, hits[0].start())})",
            fix_hint="Remove these values from log messages, or mask them (for example show only the last 4 characters)."))
    return out


# ── L10.6 insecure cookies ────────────────────────────────────────────
_SESSION_NAME = re.compile(r"sess|sid\b|token|auth|jwt|login|access|refresh|remember", re.I)
_SET_COOKIE = re.compile(r"\b(?:res|response|reply)\.(?:cookie|set_cookie|setCookie)\(\s*[\"'`]([\w.-]+)[\"'`]")
_DOC_COOKIE = re.compile(r"document\.cookie\s*=\s*[\"'`]\s*([\w.-]+)\s*=")
_EXPRESS_SESSION = re.compile(r"\bsession\(\s*\{")
_DJANGO_COOKIE_OFF = re.compile(r"^\s*(?:SESSION_COOKIE_(?:SECURE|HTTPONLY)|CSRF_COOKIE_SECURE)\s*=\s*False\b", re.M)
_FLAGS = {"httponly": re.compile(r"http_?only\s*[:=]\s*(?:true|True)", re.I),
          "secure": re.compile(r"\bsecure\s*[:=]\s*(?!false|False)\S", re.I),
          "samesite": re.compile(r"same_?site\s*[:=]\s*[\"']?(?:lax|strict|none)", re.I)}


def _missing_flags(args: str) -> list[str]:
    return [label for label, rx in _FLAGS.items() if not rx.search(args)]


def _top_level_args(args: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for c in args:
        depth += c in "([{" and 1 or c in ")]}" and -1 or 0
        if c == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
    return out + [cur.strip()] if cur.strip() else out


def _options_from_helper(args: str) -> bool:
    """`res.cookie(n, v, cookieOpts())` / `set_cookie(n, v, **kwargs())`: the flags live
    elsewhere, so we can't say they're missing."""
    parts = _top_level_args(args)
    return "**" in args or (len(parts) >= 3 and not parts[2].startswith("{"))


def _cookie_findings(path: str, text: str) -> list[tuple[int, str]]:
    found = []
    for m in _SET_COOKIE.finditer(text):
        args = _call_args(text, text.index("(", m.start()))
        missing = _missing_flags(args)
        if _SESSION_NAME.search(m.group(1)) and missing and not _options_from_helper(args):
            found.append((m.start(), f"cookie `{m.group(1)}` is set without {', '.join(missing)}"))
    for m in _DOC_COOKIE.finditer(text):
        if _SESSION_NAME.search(m.group(1)):
            found.append((m.start(), f"cookie `{m.group(1)}` is set from browser code, so it can't be HttpOnly"))
    for m in _EXPRESS_SESSION.finditer(text):
        if not _FLAGS["secure"].search(_call_args(text, text.index("(", m.start()))):
            found.append((m.start(), "session cookie has no `secure` setting"))
    for m in _DJANGO_COOKIE_OFF.finditer(text):
        found.append((m.start(), f"`{m.group(0).strip()}`"))
    return found


def check_insecure_cookies(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        if not path.endswith(_CODE_EXT) or _SKIP.search(path):
            continue
        for idx, what in _cookie_findings(path, text):
            out.append(_item(
                scanner="privacy", rule_id="insecure_session_cookie", severity="high",
                title="Login cookies can be stolen or sent over unprotected connections",
                impact="A script injected into a page, or someone on the same Wi-Fi, could copy a logged-in user's session and act as them.",
                file=path, line=_line_of(text, idx), detail=what,
                fix_hint="Set HttpOnly, Secure and SameSite on every login/session cookie."))
    return out


CHECKS = [
    ("safe", "sensitive_logs", check_sensitive_logs),
    ("safe", "insecure_cookies", check_insecure_cookies),
]
