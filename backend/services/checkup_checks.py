"""
services/checkup_checks.py — deterministic checkup checks (S12: no LLM
decides whether something is a problem).

Each check is a pure function `(text_cache) -> list[item]` over the
`{path: content}` map from `repo_text_cache.build_text_cache`.  Items are
in the `fix_items` input shape and carry plain-English copy (S10); raw
detail lives in `evidence`.  Checks prefer a missed issue over a false
alarm: a founder who is told "your app is unsafe" wrongly stops trusting
the verdict.  Helpers live in `checkup_common`; the "does it run?"
checks in `checkup_checks_runs`.
"""
from __future__ import annotations

import re
from typing import Callable

from services.checkup_checks_secrets import check_env_files, check_secrets_in_history
from services.checkup_checks_runs import (  # noqa: F401  (re-exported)
    check_dead_buttons, check_missing_routes, check_undeclared_packages,
    check_unresolved_imports,
)
from services.checkup_common import (  # noqa: F401  (re-exported for callers)
    _JS_EXT, _JWT_RE, _files, _is_client, _item, _jwt_role, _line_of, detect_stack,
)

# ── safe ──────────────────────────────────────────────────────────────
_SR_ENV_RE = re.compile(r"(?:VITE|NEXT_PUBLIC|REACT_APP|PUBLIC)_[A-Z0-9_]*SERVICE_ROLE[A-Z0-9_]*")


def check_service_role_in_client(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        if not _is_client(path) or path.endswith((".md", ".lock")):
            continue
        for m in _JWT_RE.finditer(text):
            if _jwt_role(m.group(1)) == "service_role":
                out.append(_item(
                    scanner="security", rule_id="service_role_key_in_client", severity="critical",
                    title="A master key to your database is visible to every visitor",
                    impact="Anyone who opens your site can copy it and read, change or delete all of your data.",
                    file=path, line=_line_of(text, m.start()),
                    detail="service_role key embedded in client-side code (value not shown)",
                    fix_hint="Move it to a server-only secret and rotate the key."))
        for m in _SR_ENV_RE.finditer(text):
            out.append(_item(
                scanner="security", rule_id="service_role_env_exposed", severity="critical",
                title="A master key to your database is set up to be sent to visitors' browsers",
                impact="Variables with this prefix are bundled into the public site, so the key becomes public.",
                file=path, line=_line_of(text, m.start()),
                detail=f"public-prefixed env var name: {m.group(0)}",
                fix_hint="Rename without the public prefix and use it only on a server."))
    return out


_CORS_PY = re.compile(r"allow_origins\s*=\s*\[\s*[\"']\*[\"']\s*\]", re.S)
_CORS_JS = re.compile(r"origin\s*:\s*[\"']\*[\"']")


def check_cors_open_with_credentials(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        if path.endswith(".py"):
            hit = _CORS_PY.search(text)
            creds = re.search(r"allow_credentials\s*=\s*True", text)
        elif path.endswith(_JS_EXT):
            hit = _CORS_JS.search(text)
            creds = re.search(r"credentials\s*:\s*true", text)
        else:
            continue
        if hit and creds:
            out.append(_item(
                scanner="security", rule_id="cors_open_with_credentials", severity="high",
                title="Any website can act as your logged-in users",
                impact="Your server accepts requests with login cookies from every other website.",
                file=path, line=_line_of(text, hit.start()),
                detail="CORS allows all origins together with credentials",
                fix_hint="List only your own site addresses."))
    return out


_ENV_REF = re.compile(r"(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]+)")


def check_env_not_documented(tc: dict[str, str]) -> list[dict]:
    example = next(((p, t) for p, t in tc.items()
                    if p.rsplit("/", 1)[-1] in (".env.example", ".env.sample", "env.example")), None)
    if not example:
        return []
    documented = set(re.findall(r"^\s*([A-Z][A-Z0-9_]+)\s*=", example[1], re.M))
    builtin = {"NODE_ENV", "MODE", "DEV", "PROD", "BASE_URL", "SSR", "CI", "PORT"}
    out, seen = [], set()
    for path, text in _files(tc):
        if not path.endswith(_JS_EXT):
            continue
        for m in _ENV_REF.finditer(text):
            name = m.group(1)
            if name in documented or name in builtin or name in seen:
                continue
            seen.add(name)
            out.append(_item(
                scanner="bug_hunt", rule_id="env_not_documented", severity="low",
                title="Someone setting this up wouldn't know a required setting exists",
                impact="A new deployment can fail with a confusing error.",
                file=path, line=_line_of(text, m.start()),
                detail=f"{name} is used but missing from the example settings file",
                confidence="medium"))
    return out


CHECKS: list[tuple[str, str, Callable[[dict[str, str]], list[dict]]]] = [
    ("runs", "unresolved_import", check_unresolved_imports),
    ("runs", "undeclared_package", check_undeclared_packages),
    ("runs", "missing_route", check_missing_routes),
    ("runs", "dead_button", check_dead_buttons),
    ("safe", "service_role", check_service_role_in_client),
    ("safe", "cors", check_cors_open_with_credentials),
    ("launch", "env_not_documented", check_env_not_documented),
    ("safe", "env_files", check_env_files),
    ("safe", "secrets_in_history", check_secrets_in_history),
]
