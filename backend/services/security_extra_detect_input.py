"""
services/security_extra_detect_input.py — Phase 3 detectors that read content
patterns: prompt injection, mass assignment, SRI, public bucket.

Each detector is `(path, lines, text) -> Iterator[(line_no, snippet)]` and is
registered in services/security_extra_rules.py.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from services.security_extra_common import HTML, JS, PY, enclosing_def, ignored, line_ok

# ── prompt injection ──────────────────────────────────────────────────
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


def _prompt_injection_line(ln: str) -> bool:
    if _EXEC_LLM.search(ln) and _LLM_OUTPUT.search(ln):
        return True
    if not _PROMPT_TARGET.search(ln) or _PI_SAFE.search(ln):
        return False
    return bool(_PY_FSTR_USER.search(ln) or _PY_FMT_USER.search(ln) or _JS_TPL_USER.search(ln))


def detect_prompt_injection(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    if not path.lower().endswith(PY + JS) or not _LLM_CALL.search(text):
        return
    for i, ln in enumerate(lines, start=1):
        if line_ok(ln) and _prompt_injection_line(ln):
            yield i, ln


# ── mass assignment ───────────────────────────────────────────────────
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
_SET_TARGET = re.compile(r"""["']\$set["']\s*:\s*(\w+)""")


def _py_mass_assignment_is_safe(lines: list[str], idx: int, pattern_no: int, line: str) -> bool:
    """Two legitimate shapes that look like mass assignment:
      * `$set: payload` where `payload` is a dict literal built in this function
        (server-chosen fields, not the request body);
      * the handler is admin-gated, so an admin editing a settings document is
        not a privilege escalation by an ordinary caller."""
    def_idx = enclosing_def(lines, idx)
    start = def_idx if def_idx is not None else idx
    if _ADMIN_GATE.search("\n".join(lines[start:idx + 1])):
        return True
    target = _SET_TARGET.search(line) if pattern_no == 0 else None
    if target is None:
        return False
    built_here = rf"\b{re.escape(target.group(1))}\s*=\s*\{{"
    return bool(re.search(built_here, "\n".join(lines[max(0, idx - 40):idx + 1])))


def _mass_assignment_line(lines: list[str], idx: int, pats: list, is_py: bool) -> bool:
    ln = lines[idx]
    if not line_ok(ln):
        return False
    hit = next((k for k, p in enumerate(pats) if p.search(ln)), None)
    if hit is None:
        return False
    return not (is_py and _py_mass_assignment_is_safe(lines, idx, hit, ln))


def detect_mass_assignment(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    low = path.lower()
    is_py = low.endswith(PY)
    if not (is_py or low.endswith(JS)):
        return
    pats = _MA_PY if is_py else _MA_JS
    for i in range(len(lines)):
        if _mass_assignment_line(lines, i, pats, is_py):
            yield i + 1, lines[i]


# ── SRI ───────────────────────────────────────────────────────────────
_TAG = re.compile(r"<(script|link)\b([^<>]*)>", re.IGNORECASE | re.DOTALL)
_INTEGRITY = re.compile(r"\bintegrity\s*=", re.IGNORECASE)
_EXTERNAL = re.compile(r"^(?:https?:)?//([^/:?#\s]+)", re.IGNORECASE)
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


def _sri_resource_url(tag: str, attrs: str) -> str | None:
    if tag == "script":
        return _attr(attrs, "src")
    rel = _attr(attrs, "rel") or ""
    return _attr(attrs, "href") if "stylesheet" in rel.lower() else None


def _sri_needs_hash(url: str | None) -> bool:
    """True for a third-party host that is not a dynamic vendor loader."""
    hm = _EXTERNAL.match((url or "").strip())
    if not hm:
        return False
    host = hm.group(1).lower()
    return host not in _EXEMPT_HOSTS and not host.endswith(_EXEMPT_SUFFIXES)


def detect_sri(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    if not path.lower().endswith(HTML) or "<" not in text:
        return
    for m in _TAG.finditer(text):
        attrs = m.group(2)
        if _INTEGRITY.search(attrs) or not _sri_needs_hash(_sri_resource_url(m.group(1).lower(), attrs)):
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        first = lines[line_no - 1] if line_no - 1 < len(lines) else ""
        if not ignored(first):
            yield line_no, first or m.group(0)


# ── public bucket ─────────────────────────────────────────────────────
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


def _public_bucket_line(lines: list[str], idx: int) -> bool:
    ln = lines[idx]
    if not line_ok(ln) or "Deny" in ln:
        return False
    if any(p.search(ln) for p in _PB_LINE):
        return True
    if not _PB_GCS_MEMBER.search(ln):
        return False
    return bool(_PB_GCS_CONTEXT.search("\n".join(lines[max(0, idx - 3):idx + 4])))


def detect_public_bucket(path: str, lines: list[str], text: str) -> Iterator[tuple[int, str]]:
    if not _PB_PREFILTER.search(text):
        return
    for i in range(len(lines)):
        if _public_bucket_line(lines, i):
            yield i + 1, lines[i]
