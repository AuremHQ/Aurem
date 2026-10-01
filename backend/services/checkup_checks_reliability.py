"""
services/checkup_checks_reliability.py — Check Library v2, layers 13/14:
L13.2 errors swallowed by empty catch blocks, L14.1 no health endpoint.

An empty catch with an explanatory comment is a recorded decision and is
left alone; so are narrow Python excepts (`except ImportError: pass`).
Health endpoints are only expected from projects that run their own
server (C4: stack-aware).
"""
from __future__ import annotations

import re

from services.checkup_common import _files, _item, _line_of, detect_stack, _package_deps

_JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_SKIP = re.compile(r"(?:^|/)(?:tests?|__tests__|fixtures?|examples?|docs?|scripts?)/|\.(?:test|spec)\.|(?:^|/)test_", re.I)

_JS_EMPTY_CATCH = re.compile(r"\bcatch\s*(?:\(\s*\w*\s*\))?\s*\{\s*\}|\.catch\(\s*(?:\(\s*\w*\s*\)|\w+)\s*=>\s*\{\s*\}\s*\)")
_PY_SWALLOW = re.compile(r"^[ \t]*except(?:[ \t]+(?:Exception|BaseException)(?:[ \t]+as[ \t]+\w+)?)?[ \t]*:[ \t]*(?:\n[ \t]+)?pass\b", re.M)


def _swallows(path: str, text: str) -> list[int]:
    rx = _PY_SWALLOW if path.endswith(".py") else _JS_EMPTY_CATCH if path.endswith(_JS_EXT) else None
    return [_line_of(text, m.start()) for m in rx.finditer(text)] if rx else []


def check_swallowed_errors(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        if _SKIP.search(path):
            continue
        lines = _swallows(path, text)
        if not lines:
            continue
        out.append(_item(
            scanner="reliability", rule_id="errors_swallowed", severity="medium",
            title="When something breaks, your app hides it instead of telling you",
            impact="Failures disappear silently: users see a blank or half-working screen and you never find out why.",
            file=path, line=lines[0],
            detail=f"{len(lines)} place(s) catch an error and do nothing with it (first at line {lines[0]})",
            fix_hint="At minimum log the error; better, report it to an error tracker and show the user a clear message."))
    return out


# ── L14.1 ─────────────────────────────────────────────────────────────
_PY_SERVER_DEPS = re.compile(r"^\s*(?:fastapi|flask|django|starlette|aiohttp|sanic)\b", re.I | re.M)
_JS_SERVER_DEPS = ("express", "fastify", "koa", "hono", "@nestjs/core", "restify")
_SERVER_INIT = re.compile(r"\bFastAPI\(|\bFlask\(\s*__name__|\bexpress\(\)|\bfastify\(|\bnew Koa\(|\bNestFactory\.create|\bnew Hono\(")
_HEALTH = re.compile(r"""["'`]/?(?:api/)?(?:health|healthz|healthcheck|health-check|ping|readyz?|livez?)["'`/?]|HEALTHCHECK\s""", re.I)


def _runs_own_server(tc: dict[str, str]) -> bool:
    deps, _n = _package_deps(tc)
    if any(d in deps for d in _JS_SERVER_DEPS):
        return True
    return any(p.endswith("requirements.txt") and _PY_SERVER_DEPS.search(t) for p, t in _files(tc))


def _server_entry(tc: dict[str, str]) -> tuple[str, int] | None:
    for path, text in _files(tc):
        if _SKIP.search(path) or not path.endswith((".py",) + _JS_EXT):
            continue
        m = _SERVER_INIT.search(text)
        if m:
            return path, _line_of(text, m.start())
    return None


def check_health_endpoint(tc: dict[str, str]) -> list[dict]:
    if not _runs_own_server(tc) or any(_HEALTH.search(t) for _p, t in _files(tc)):
        return []
    entry = _server_entry(tc)
    if entry is None:
        return []
    stack = detect_stack(tc)["label"]
    return [_item(
        scanner="reliability", rule_id="no_health_endpoint", severity="medium",
        title="Nothing can tell whether your app is actually up",
        impact="Your host and any uptime monitor can't check your app, so an outage can last hours before anyone notices.",
        file=entry[0], line=entry[1],
        detail=f"server app found ({stack}) but no /health (or /ping, /ready) endpoint",
        fix_hint="Add a tiny /health endpoint that returns 200 when the app (and its database) is reachable.")]
