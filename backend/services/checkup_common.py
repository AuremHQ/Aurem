"""
services/checkup_common.py — shared helpers for the deterministic checkup
checks: vendored-dir filtering, client/server path hints, the item shape
and stack detection (S9: honest about what we can / can't verify).
"""
from __future__ import annotations

import base64
import json
import re
from typing import Iterator, Optional

from services.checkup_meta import is_meta_key

_SKIP_DIRS = ("node_modules/", "vendor/", ".agent/", "dist/", "build/",
              "benchmarks/", ".next/", "coverage/", ".git/")
_JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_CLIENT_HINTS = ("src/", "app/", "pages/", "components/", "public/", "frontend/", "client/")
_SERVER_HINTS = ("api/", "server/", "backend/", "supabase/functions/", "functions/",
                 "scripts/", "migrations/")

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.(eyJ[A-Za-z0-9_-]{8,})\.[A-Za-z0-9_-]{8,}")


def _is_vendored(path: str) -> bool:
    p = "/" + path.lstrip("/")
    return any(("/" + d) in p for d in _SKIP_DIRS)


def _files(tc: dict[str, str]) -> Iterator[tuple[str, str]]:
    for path, text in tc.items():
        if isinstance(text, str) and not is_meta_key(path) and not _is_vendored(path):
            yield path, text


def _is_client(path: str) -> bool:
    p = path.lstrip("/")
    if any(("/" + s) in ("/" + p) for s in _SERVER_HINTS):
        return False
    return p.startswith(_CLIENT_HINTS) or any(("/" + h) in p for h in _CLIENT_HINTS)


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _item(*, scanner, rule_id, severity, title, impact, file, line, detail,
          confidence="high", fix_hint=None) -> dict:
    return {
        "scanner": scanner, "rule_id": rule_id, "severity": severity,
        "title_plain": title, "impact_plain": impact,
        "evidence": {"file": file, "line": line, "rule": rule_id, "detail": detail},
        "confidence": confidence,
        "fix": {"hint": fix_hint} if fix_hint else None,
    }


def _jwt_role(payload_b64: str) -> str:
    try:
        pad = "=" * (-len(payload_b64) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload_b64 + pad)).get("role", ""))
    except (ValueError, TypeError):
        return ""


_DEP_KEYS = ("dependencies", "devDependencies")


def _package_deps(tc: dict[str, str]) -> tuple[set[str], int]:
    """(all declared dependency names, number of package.json files)."""
    deps: set[str] = set()
    count = 0
    for path, text in _files(tc):
        if not path.endswith("package.json"):
            continue
        count += 1
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            continue
        for key in _DEP_KEYS:
            deps |= set((parsed.get(key) or {}).keys())
    return deps, count


def _has_supabase(tc: dict[str, str], deps: set[str]) -> bool:
    if "@supabase/supabase-js" in deps:
        return True
    return any(p.lstrip("/").startswith("supabase/") or "/supabase/migrations/" in p
               for p, _ in _files(tc))


def _stack_label(frontend: Optional[str], supabase: bool, py: bool) -> str:
    parts = [frontend, "supabase" if supabase else None, "python" if py else None]
    return " + ".join(x for x in parts if x) or "unknown"


def detect_stack(tc: dict[str, str]) -> dict:
    deps, pkg_count = _package_deps(tc)
    py = any(p.endswith(("requirements.txt", "pyproject.toml")) for p, _ in _files(tc))
    supabase = _has_supabase(tc, deps)
    frontend = next((n for n in ("next", "react", "vue", "svelte") if n in deps), None)
    return {
        "frontend": frontend,
        "backend_python": py,
        "supabase": supabase,
        "has_package_json": pkg_count > 0,
        "label": _stack_label(frontend, supabase, py),
    }
