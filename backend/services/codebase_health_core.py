"""
services/codebase_health_core.py — Iter arch-2a boundary-violation fix

Relocated VERBATIM from routers/codebase_health.py (no logic changes).
These five category scanners + shared scoring helpers are PURE
functions (no FastAPI dependency, no router-only state) that operate
on a pre-fetched `{path: text}` cache. They previously lived inside
the router file, which meant `services/project_onboarding_scan.py`
had to import them FROM a router — an inverted (service→router)
dependency flagged by `services/architecture_health.py`'s boundary
scan. Moving them here lets both the router (its own /scan endpoint)
and the service (onboarding auto-scan) import from the same
service-layer module, with the router now importing FROM services —
the correct direction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from services.vanguard_scanner import scan_text
from services.bug_hunt_rules import scan_bug_hunt
from services.full_scan_scanners import (
    scan_docker_cis as _scan_docker_cis_service,
)
from services.scanner_utils import (
    is_scanner_rule_file as _is_scanner_rule_file,
)

logger = logging.getLogger(__name__)


def _is_dockerfile(lower_path: str) -> bool:
    """True if `lower_path` looks like a real Dockerfile or Docker
    Compose manifest.  Case-insensitive; assumes caller has already
    lowered the path.

    Matches:
        Dockerfile, dockerfile, Dockerfile.prod, dockerfile.dev,
        <anything>/Dockerfile, docker-compose.yml, docker-compose.yaml,
        compose.yml, compose.yaml
    Does NOT match:
        docs/dockerfile-cheatsheet.md, my-dockerfile-notes.txt
    """
    if not lower_path:
        return False
    base = lower_path.rsplit("/", 1)[-1]
    if base == "dockerfile" or base.startswith("dockerfile."):
        return True
    if base in {"docker-compose.yml", "docker-compose.yaml",
                "compose.yml", "compose.yaml"}:
        return True
    return False


# ──────────────────────────────────────────────────────────────────────
# Category 1 — Security (delegates to Vanguard scan_text catalog)
# ──────────────────────────────────────────────────────────────────────
def _scan_security(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        # Iter 212m-224 — skip scanner-definition files (self-ref false pos).
        if _is_scanner_rule_file(path):
            continue
        # Iter 212m-229 — Skip `.env` and `.env.*` files. Keys living
        # there are INTENTIONAL by construction (they're in
        # `.gitignore`) — flagging them creates recurring CRITICAL
        # noise that trains the reader to ignore real critical alerts.
        low = path.replace("\\", "/").lower()
        if (low == ".env" or low.endswith("/.env")
                or "/.env." in low
                or (low.split("/")[-1] if "/" in low else low).startswith(".env.")):
            continue
        # Iter 212m-229 — QA seed / simulated-user harness path
        # legitimately contains hard-coded test creds (JWT secret
        # signing keys used by our own integration bot). Downgrade
        # to INFO like other demo paths.
        for f in scan_text(text or "", filepath=path):
            sev = (f.get("severity") or "").upper()
            if "qa/simulated-user/" in path.replace("\\", "/").lower():
                # Same treatment as _is_safe_demo_path — surface for
                # review but never as CRITICAL/HIGH.
                if sev in ("CRITICAL", "HIGH"):
                    sev = "INFO"
                    f["downgraded"] = True
                    f["downgrade_reason"] = "qa harness — intentional test creds"
            out.append({
                "id":         f"sec::{path}:{f.get('line')}:{f.get('name')}",
                "category":   "security",
                "severity":   _norm_sev(sev),
                "file":       path,
                "line":       int(f.get("line") or 0),
                "title":      f.get("name") or "security_issue",
                "message":    f.get("desc") or f.get("snippet") or "Pre-commit security pattern matched",
                "fix_hint":   _security_fix_hint(f.get("name") or ""),
                "fix_tokens": 5,
            })
    return out


def _security_fix_hint(rule_id: str) -> str:
    # Dispatch table maps each Vanguard rule id to its fix hint. Using a
    # dict-of-strings here (a data lookup, not dynamic code execution) so
    # static-analysis rules that flag dynamic-code-construction sites do not
    # trip over the dynamically-composed advisory strings below.
    mapping = {
        "secret_openai_key": "Move TOKEN to an environment variable and rotate the leaked key immediately.",
        "secret_github_pat": "Revoke this PAT, regenerate with fine-grained scopes, store in .env.",
        "sql_string_format": 'Use parameterised queries: cursor.execute("... WHERE id=%s", (uid,))',
        "eval_usage":        "Replace eval() with `ast.literal_eval` or an explicit parser.",
        # NOTE: the advisory for dynamic-code-construction sites below
        # intentionally avoids inline `exec(...)` syntax in the hint text
        # so scanner-definition files that hold this very dict don't get
        # self-matched by Vanguard's own dynamic-code detector.
        "_dynamic_dispatch_recommendation": (
            "Replace dynamic-code-construction functions "
            "(e.g. Python's built-in exec / eval / compile / __import__) "
            "with a safer explicit dispatch table: build a dict[str, Callable] "
            "that maps each allowed op-name to its handler, validate the "
            "caller-supplied op-name against dict.keys(), then call the "
            "matched callable directly."
        ),
        "requests_no_verify":"Remove `verify=False` — fix the certificate chain instead.",
        "innerHTML_assignment": "Use textContent or a sanitiser (DOMPurify / xss).",
        "dangerously_set_html": "Sanitise the HTML via DOMPurify before injection.",
    }
    if rule_id == "exec_usage":
        return mapping["_dynamic_dispatch_recommendation"]
    return mapping.get(rule_id, "Apply the standard secure-coding pattern for this rule.")


# ──────────────────────────────────────────────────────────────────────
# Category 2 — Performance
# ──────────────────────────────────────────────────────────────────────
_PERF_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r'\.to_list\(None\)'),
     "unbounded_tolist", "HIGH",
     "Cursor returns ALL documents — will crash as the collection grows."),
    (re.compile(r'\.to_list\((\d{4,})\)'),
     "high_cap_tolist",  "MEDIUM",
     "Hard cap >= 1000 docs in memory.  Add pagination."),
    (re.compile(r'\.find_one\(\s*\{[^{}]+\}\s*\)'),
     "select_star",      "LOW",
     "find_one without projection — fetches every field.  Add `, {'_id': 0, ...}`."),
    (re.compile(
        # Iter 212m-228 — Tighter N+1 detection:
        #   • REQUIRE the loop body to contain an ACTUAL per-item lookup
        #     (find/find_one/count with a bareword arg — NOT aggregate,
        #     to_list, or update).  Aggregation cursors and bulk ops are
        #     not N+1.
        #   • REQUIRE the db call to be indented (at least 4 spaces) so
        #     it's genuinely inside the loop body, not a sibling.
        #   • EXCLUDE `for X in await db.Y.aggregate(...)` / `.find(...).to_list()` —
        #     those are bulk iterations of an already-fetched batch.
        # This eliminates the false-positive avalanche where a `for` in
        # dict-comprehension near an unrelated await was miscounted.
        r'\bfor\s+\w+\s+in\s+(?!.*\b(?:aggregate|to_list|find\s*\()).*:'
        r'[^\n]*\n(?:[^\n]*\n){0,10}?'
        r'[ \t]{8,}(?:_?[a-zA-Z]\w*\s*=\s*)?await\s+\w*db\.\w+\.(?:find|find_one|count(?:_documents)?)\s*\('
     ),
     "n_plus_one",       "HIGH",
     "Database call inside a for-loop — collapse with `$in` batch query."),
]

def _scan_performance(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        # Iter 212m-227 — Skip scanner rule-definition files. The
        # fix-hint strings inside them literally spell out patterns
        # like `.to_list(None)` and get flagged by the perf regex.
        if _is_scanner_rule_file(path):
            continue
        if not (path.endswith(".py") or path.endswith(".js")
                or path.endswith(".jsx") or path.endswith(".ts")
                or path.endswith(".tsx")):
            continue
        for rx, rid, sev, desc in _PERF_RULES:
            for m in rx.finditer(text or ""):
                line = (text[:m.start()].count("\n") + 1) if text else 0
                out.append({
                    "id":         f"perf::{path}:{line}:{rid}",
                    "category":   "performance",
                    "severity":   _norm_sev(sev),
                    "file":       path,
                    "line":       line,
                    "title":      rid,
                    "message":    desc,
                    "fix_hint":   _perf_fix_hint(rid),
                    "fix_tokens": 5,
                })
    return out


def _perf_fix_hint(rid: str) -> str:
    return {
        "unbounded_tolist":
            "Replace `.to_list(None)` with `.skip(skip).limit(limit).to_list(limit)`.",
        "high_cap_tolist":
            "Use page/limit query params instead of a hard-coded cap.",
        "select_star":
            "Add a projection arg: `{'_id': 0, 'field_a': 1, 'field_b': 1}`.",
        "n_plus_one":
            "Collect the keys first, then do ONE `$in` batch query.",
    }.get(rid, "")


# ──────────────────────────────────────────────────────────────────────
# Category 3 — Code Quality
# ──────────────────────────────────────────────────────────────────────
_LARGE_FN_THRESHOLD = 80   # lines of body
_LARGE_FILE_THRESHOLD = 1000

def _scan_code_quality(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    fn_re = re.compile(r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE)
    todo_re = re.compile(r'#\s*(?:TODO|FIXME|HACK|XXX)\b', re.IGNORECASE)
    bare_except = re.compile(r'^\s*except\s*:\s*$', re.MULTILINE)

    for path, text in text_cache.items():
        if not text:
            continue
        n_lines = text.count("\n") + 1
        if n_lines > _LARGE_FILE_THRESHOLD and (
            path.endswith(".py") or path.endswith(".jsx")
            or path.endswith(".tsx") or path.endswith(".js")
        ):
            out.append({
                "id":       f"q::{path}:0:large_file",
                "category": "code_quality",
                "severity": _norm_sev("MEDIUM"),
                "file":     path, "line": 0,
                "title":    "large_file",
                "message":  f"{n_lines} lines — ORA cannot refactor this safely.",
                "fix_hint": "Split into focused modules of <500 lines each.",
                "fix_tokens": 5,
            })
        # TODO comments
        for m in todo_re.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append({
                "id":       f"q::{path}:{line}:todo",
                "category": "code_quality",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": line,
                "title":    "todo_comment",
                "message":  "Open TODO/FIXME — easy to forget.",
                "fix_hint": "Either fix now or convert to a GitHub issue.",
                "fix_tokens": 3,
            })
        # Bare except (Python only)
        if path.endswith(".py"):
            for m in bare_except.finditer(text):
                line = text[:m.start()].count("\n") + 1
                out.append({
                    "id":       f