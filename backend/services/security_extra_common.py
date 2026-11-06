"""
services/security_extra_common.py — shared helpers for the Phase 3 static rules.

Used by services/security_extra_detect_input.py and
services/security_extra_detect_routes.py (detectors) and wired together in
services/security_extra_rules.py. Pure functions, stdlib only.
"""
from __future__ import annotations

import re

IGNORE_MARKERS = ("vanguard: ignore", "security-scan: ignore")
MAX_LINE = 2000

PY = (".py",)
JS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
HTML = (".html", ".htm")

JS_ROUTE = re.compile(r"""\b(?:app|router|server)\.(get|post|put|patch|delete)\(\s*[\"'`]([^\"'`]*)[\"'`]""")
_DEF = re.compile(r"\s*(?:async\s+)?def\s")
_JS_BLOCK_END = re.compile(r"^\}\)?;?\s*$")
_BLOCK_SPAN = 150


def ignored(line: str) -> bool:
    return any(m in line for m in IGNORE_MARKERS)


def line_ok(line: str) -> bool:
    """A line a detector may look at: not huge (minified) and not suppressed."""
    return len(line) <= MAX_LINE and not ignored(line)


def _blank_or_comment(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def py_block(lines: list[str], start: int) -> tuple[int, int]:
    """[start, end) of the function that follows the decorator at `start`."""
    limit = min(len(lines), start + _BLOCK_SPAN)
    j, seen_def = start + 1, False
    while j < limit:
        ln = lines[j]
        if not _blank_or_comment(ln):
            if seen_def and ln[0] not in " \t)]}":
                break
            seen_def = seen_def or bool(_DEF.match(ln))
        j += 1
    return start, j


def js_block(lines: list[str], start: int) -> tuple[int, int]:
    """[start, end) of the route handler that starts on line `start`."""
    limit = min(len(lines), start + _BLOCK_SPAN)
    j = start + 1
    while j < limit:
        ln = lines[j]
        if JS_ROUTE.search(ln):
            break
        if _JS_BLOCK_END.match(ln):
            j += 1
            break
        j += 1
    return start, j


def enclosing_def(lines: list[str], idx: int, back: int = 40) -> int | None:
    """Index of the nearest `def` at or above `idx` (within `back` lines)."""
    for k in range(idx, max(0, idx - back) - 1, -1):
        if _DEF.match(lines[k]):
            return k
    return None
