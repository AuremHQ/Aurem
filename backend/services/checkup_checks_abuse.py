"""
services/checkup_checks_abuse.py — Check Library v2, layer 11 (abuse and
cost): L11.1 no rate limit on login/signup/reset, L11.3 paid AI calls with
no per-user quota.  Detection only; fixes are plan_first.

Stack-aware (C4): a project whose login is hosted by a provider (Supabase
Auth, Clerk, Auth0, Firebase) has no login endpoint of its own and is
silent for L11.1.  If limiting/quota evidence exists anywhere in the repo
but not beside the endpoint, we can't tell whether it applies, so the item
is `unverified` for L11.3 and silent for L11.1 (a global middleware is the
common shape).
"""
from __future__ import annotations

import re
from typing import Iterator

from services.checkup_common import _files, _is_client, _item, _line_of

_CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_SKIP = re.compile(r"(?:^|/)(?:tests?|__tests__|fixtures?|examples?|docs?|scripts?)/|\.(?:test|spec)\.|(?:^|/)test_", re.I)

_AUTH_ROUTE = re.compile(
    r"(?:\.post|\.route|\.all|\.use)\(\s*[\"'`][^\"'`]*(?:login|log-in|signin|sign-in|signup|sign-up|register|forgot|reset-?password)"
    r"|@\w+\.(?:post|route)\(\s*[\"'][^\"']*(?:login|signin|signup|register|forgot|reset)", re.I)
_AUTH_FILE = re.compile(r"(?:^|/)api/auth/(?:login|signin|signup|register|forgot|reset)[^/]*(?:/route)?\.(?:ts|js)$", re.I)
_LIMITER = re.compile(
    r"express-rate-limit|rate-limiter-flexible|express-slow-down|@upstash/ratelimit|slowapi|fastapi-limiter|django-ratelimit|"
    r"flask-limiter|\brate_?limit|\bratelimit|\bLimiter\(|\bthrottl|\blimiter\b", re.I)

_LLM_CALL = re.compile(
    r"chat\.completions\.create|\.responses\.create|\bmessages\.create\(|ChatCompletion\.create|generateContent\(|"
    r"\bstreamText\(|\bgenerateText\(|openrouter\.ai/api|api\.openai\.com|api\.anthropic\.com", re.I)
_QUOTA = re.compile(
    r"quota|\bcredits?\b|usage_?limit|daily_?limit|monthly_?limit|token_?budget|spend_?cap|max_?spend|"
    r"\bbudget\b|check_?usage|usage_?count|per_?user_?limit", re.I)


def _server_code(tc: dict[str, str]) -> Iterator[tuple[str, str]]:
    for path, text in _files(tc):
        if path.endswith(_CODE_EXT) and not _SKIP.search(path):
            yield path, text


def _repo_has(tc: dict[str, str], rx: re.Pattern, exclude: str = "") -> bool:
    return any(rx.search(t) for p, t in _files(tc) if p != exclude and not _SKIP.search(p))


def _auth_endpoint_line(path: str, text: str) -> int | None:
    m = _AUTH_ROUTE.search(text)
    if m:
        return _line_of(text, m.start())
    return 1 if _AUTH_FILE.search(path) else None


def check_auth_rate_limit(tc: dict[str, str]) -> list[dict]:
    if _repo_has(tc, _LIMITER):
        return []
    out = []
    for path, text in _server_code(tc):
        line = _auth_endpoint_line(path, text)
        if line is None:
            continue
        out.append(_item(
            scanner="abuse", rule_id="auth_no_rate_limit", severity="high",
            title="Anyone can try thousands of passwords against your login",
            impact="Attackers can guess passwords or flood sign-up and password-reset emails (which you pay for) with no slowdown.",
            file=path, line=line,
            detail="login/sign-up/reset endpoint found and no rate limiting anywhere in the project",
            fix_hint="Limit attempts per IP and per account (for example 5 per minute) on login, sign-up and password reset."))
    return out


def check_ai_cost_protection(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _server_code(tc):
        m = _LLM_CALL.search(text)
        if not m or _is_client(path) or _QUOTA.search(text) or _LIMITER.search(text):
            continue
        elsewhere = _repo_has(tc, _QUOTA, path) or _repo_has(tc, _LIMITER, path)
        out.append(_item(
            scanner="abuse", rule_id="ai_calls_no_quota", severity="critical",
            title="One user (or a bot) could run up a huge AI bill on your account",
            impact="Every AI request costs you money. With no per-user limit, a single script can burn hundreds of dollars overnight.",
            file=path, line=_line_of(text, m.start()),
            detail="paid AI call without any per-user quota or rate limit nearby"
                   + (" (limits exist elsewhere in the project, couldn't tie them to this call)" if elsewhere else ""),
            confidence="unverified" if elsewhere else "high",
            fix_hint="Give each user a daily/monthly allowance, cap request size, and set a spending limit in your AI provider's dashboard."))
    return out
