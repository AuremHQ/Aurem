#!/usr/bin/env python3
"""
backend/scripts/lint_absolute_claims.py — Trust & Verification round,
Enhancement 6 (Absolute-Claim CI Lint Rule).

Scans user-visible copy / response templates / generated-text
templates (frontend .jsx/.js/.tsx source and backend .py string
literals that ship user-facing text) for unsupported absolute
reliability promises:

    100%  |  guaranteed  |  always works  |  100 percent  |
    never fails  |  fully reliable

Case-insensitive, punctuation/spacing-normalized. Deliberately does
NOT flag legitimate CSS/measured-runtime-stat uses of "100%" (see
`_is_css_or_stat_context`) — those are excluded by context, not by a
blanket exemption.

IMPORTANT LIMITATION (spec Section 8): this is a STATIC scan of
committed template/source text. It cannot guarantee that arbitrary
RUNTIME model output never makes an absolute claim — that is a
completely separate problem, owned by the structured completion gate
(services/verification.py) and the chat-side output guards
(services/business_voice_filter.py etc.), not by this script. Do not
advertise a clean run of this lint as proof that live chat replies
never contain an absolute claim.

Usage:
    python backend/scripts/lint_absolute_claims.py
Exit code 0 = clean (or only allowlisted matches). Exit code 1 = at
least one un-allowlisted violation — prints file:line + matched rule.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = REPO_ROOT / "backend" / "absolute_claims_allowlist.json"

SCAN_ROOTS = [
    REPO_ROOT / "frontend" / "src",
    REPO_ROOT / "backend",
]
SCAN_EXTS = {".jsx", ".js", ".tsx", ".ts", ".py"}

EXCLUDE_DIR_PARTS = {
    "node_modules", "build", "dist", ".git", "__pycache__",
    "tests", "test", "e2e", "__tests__", ".venv", "venv",
}
# This script and its own allowlist obviously reference the banned
# phrases by name — never scan itself.
EXCLUDE_FILES = {Path(__file__).resolve(), ALLOWLIST_PATH}

_PY_TRIPLE_RE = re.compile(r'("""|\'\'\')(?:.*?)\1', re.DOTALL)
_PY_COMMENT_RE = re.compile(r'(?<!["\'])#.*$')
_JS_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)
_JS_LINE_COMMENT_RE = re.compile(r'(?<!:)//.*$')


def _strip_non_shipped_text(text: str, suffix: str) -> str:
    """Removes Python docstrings/comments or JS/JSX comments BEFORE
    scanning, preserving line COUNT (multi-line matches are replaced
    with the same number of newlines) so reported line numbers still
    point at the original file. This is the "syntax-aware-ish"
    narrowing the spec calls for — comments and docstrings are
    internal engineering notes, never user-visible copy, so they
    should never have been in scope in the first place. KNOWN
    LIMITATION: a triple-quoted Python string used to hold real
    user/LLM-facing copy (e.g. a system-prompt constant) is also
    stripped by this heuristic and therefore NOT covered by this
    static check — that class of text is guarded separately by the
    runtime output guards (services/business_voice_filter.py) and
    the independent verifier (services/verification.py), not by this
    script."""
    if suffix == ".py":
        def _blank(m: "re.Match") -> str:
            return "\n" * m.group(0).count("\n")
        text = _PY_TRIPLE_RE.sub(_blank, text)
        return "\n".join(_PY_COMMENT_RE.sub("", ln) for ln in text.splitlines())
    def _blank_js(m: "re.Match") -> str:
        return "\n" * m.group(0).count("\n")
    text = _JS_BLOCK_COMMENT_RE.sub(_blank_js, text)
    return "\n".join(_JS_LINE_COMMENT_RE.sub("", ln) for ln in text.splitlines())

# ── banned phrases ──────────────────────────────────────────────────
_WORD_RULES = [
    ("guaranteed", re.compile(r"\bguaranteed\b", re.IGNORECASE)),
    ("always_works", re.compile(r"\balways\s+works?\b", re.IGNORECASE)),
    ("never_fails", re.compile(r"\bnever\s+fails?\b", re.IGNORECASE)),
    ("fully_reliable", re.compile(r"\bfully\s+reliable\b", re.IGNORECASE)),
    ("hundred_percent_reliable", re.compile(
        r"\b100\s*(%|percent)\s+(reliable|guaranteed|uptime|success|accurate)\b",
        re.IGNORECASE)),
]
# Bare "100%"/"100 percent" gets its own rule with a context exclusion
# (CSS/measured-stat), rather than being lumped into the phrases above.
_BARE_100_RE = re.compile(r"\b100\s*(%|percent)\b", re.IGNORECASE)

# CSS-like / JSON-property-value context: `width: 100%`, `: "100%"`,
# `w-[100%]`, `max-width:100%` — a bare numeric CSS value, not a claim.
# Also excludes URL-encoded percent signs inside data-URIs/SVG markup
# (`100%25` — the literal text is "100" + the URL-escape for "%", not
# a reliability claim at all).
_CSS_CONTEXT_RE = re.compile(
    r"""(?:
        [a-zA-Z-]+\s*:\s*['"]?\s*100\s*%          |   # width: 100%
        \[\s*['"]?100\s*%['"]?\s*\]                |   # w-[100%]
        ['"]?\s*100\s*%\s*['"]?\s*[,}\)]            |   # '100%', or 100%)
        100\s*%25                                       # URL-encoded 100%
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _is_css_or_stat_context(line: str, match_start: int, match_end: int) -> bool:
    """True when the bare "100%"/"100 percent" match is a CSS/style
    value or a JSON-ish property value, not a reliability claim."""
    window = line[max(0, match_start - 24):min(len(line), match_end + 8)]
    return bool(_CSS_CONTEXT_RE.search(window))


def _iter_files():
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir() or path.suffix not in SCAN_EXTS:
                continue
            if path.resolve() in EXCLUDE_FILES:
                continue
            if any(part in EXCLUDE_DIR_PARTS for part in path.parts):
                continue
            yield path


def _load_allowlist() -> dict:
    if not ALLOWLIST_PATH.exists():
        return {"entries": []}
    try:
        return json.loads(ALLOWLIST_PATH.read_text())
    except Exception as e:                                   # noqa: BLE001
        print(f"::warning::could not parse allowlist ({e}); treating as empty")
        return {"entries": []}


def _is_allowlisted(entries: list, rel_path: str, rule: str) -> bool:
    for e in entries:
        if e.get("file") == rel_path and e.get("rule") == rule:
            return True
    return False


def main() -> int:
    allowlist = _load_allowlist()
    entries = allowlist.get("entries", [])
    used_entries = set()
    violations = []

    for path in _iter_files():
        rel_path = str(path.relative_to(REPO_ROOT))
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:                                    # noqa: BLE001
            continue
        text = _strip_non_shipped_text(text, path.suffix)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for rule, pattern in _WORD_RULES:
                for m in pattern.finditer(line):
                    violations.append((rel_path, lineno, rule, m.group(0)))
            for m in _BARE_100_RE.finditer(line):
                if _is_css_or_stat_context(line, m.start(), m.end()):
                    continue
                violations.append((rel_path, lineno, "bare_100_percent", m.group(0)))

    real_violations = []
    for rel_path, lineno, rule, matched in violations:
        if _is_allowlisted(entries, rel_path, rule):
            used_entries.add((rel_path, rule))
            continue
        real_violations.append((rel_path, lineno, rule, matched))

    # Report unused allowlist entries (spec: "Report unused entries").
    for e in entries:
        key = (e.get("file"), e.get("rule"))
        if key not in used_entries:
            print(f"::warning::unused absolute-claims allowlist entry: "
                  f"{e.get('file')} / {e.get('rule')} (owner={e.get('owner')})")

    if real_violations:
        print(f"❌ {len(real_violations)} absolute-claim lint violation(s):\n")
        for rel_path, lineno, rule, matched in real_violations:
            print(f"  {rel_path}:{lineno}  [{rule}]  matched: {matched!r}")
        print(
            "\nRemove the unsupported claim, or add a justified, "
            "owned, expiring entry to backend/absolute_claims_allowlist.json "
            "if this is a genuine false positive."
        )
        return 1

    print(f"✅ absolute-claim lint clean ({len(violations)} matches, all allowlisted or excluded)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
