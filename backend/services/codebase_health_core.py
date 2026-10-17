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
    mapping = {
        "secret_openai_key": "Move TOKEN to an environment variable and rotate the leaked key immediately.",
        "secret_github_pat": "Revoke this PAT, regenerate with fine-grained scopes, store in .env.",
        "sql_string_format": 'Use parameterised queries: cursor.execute("... WHERE id=%s", (uid,))',
        "eval_usage":        "Replace eval() with `ast.literal_eval` or an explicit parser.",
        "exec_usage":        "Replace exec() with a safer dispatch (dict of callables).",
        "requests_no_verify":"Remove `verify=False` — fix the certificate chain instead.",
        "innerHTML_assignment": "Use textContent or a sanitiser (DOMPurify / xss).",
        "dangerously_set_html": "Sanitise the HTML via DOMPurify before injection.",
    }
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
                    "id":       f"q::{path}:{line}:bare_except",
                    "category": "code_quality",
                    "severity": _norm_sev("MEDIUM"),
                    "file":     path, "line": line,
                    "title":    "bare_except",
                    "message":  "Bare `except:` swallows KeyboardInterrupt + SystemExit too.",
                    "fix_hint": "Catch `Exception` and log the traceback.",
                    "fix_tokens": 5,
                })
        # Large functions (Python only — JS is harder without an AST)
        if path.endswith(".py"):
            fn_starts = [(m.start(), m.group(1)) for m in fn_re.finditer(text)]
            for i, (start, name) in enumerate(fn_starts):
                end = fn_starts[i+1][0] if i+1 < len(fn_starts) else len(text)
                body_lines = text[start:end].count("\n")
                if body_lines > _LARGE_FN_THRESHOLD:
                    line = text[:start].count("\n") + 1
                    out.append({
                        "id":       f"q::{path}:{line}:large_fn",
                        "category": "code_quality",
                        "severity": _norm_sev("MEDIUM"),
                        "file":     path, "line": line,
                        "title":    "large_function",
                        "message":  f"`{name}` is {body_lines} lines — too big to test/refactor.",
                        "fix_hint": "Split into smaller helpers; each function should do one thing.",
                        "fix_tokens": 5,
                    })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 4 — Dependencies
# ──────────────────────────────────────────────────────────────────────
# Hard-coded CVE map for known-vulnerable versions. In a future iter
# this should hit the OSV.dev API live.
_CVE_DB: dict[str, list[tuple[str, str, str]]] = {
    # package: [(matched_version_prefix, CVE_id, severity)]
    "requests":  [("2.28.", "CVE-2023-32681", "HIGH")],
    "fastapi":   [("0.95.", "outdated", "MEDIUM")],
    "pyjwt":     [("1.",    "CVE-2022-29217", "CRITICAL")],
    "axios":     [("0.21.", "CVE-2021-3749",  "HIGH")],
    "lodash":    [("4.17.20", "CVE-2021-23337", "HIGH")],
    "next":      [("13.4.", "CVE-2024-46982", "HIGH")],
    "vite":      [("4.",    "outdated", "LOW")],
}


# ── Ticket B (2026-02) — live OSV.dev CVE lookup, in-process cache ─────
# `_scan_dependencies` MUST stay a synchronous, zero-network-I/O pure
# function — it's called via `asyncio.to_thread()` from
# `routers/codebase_health.py` (safe for blocking I/O), but called
# DIRECTLY on the event loop from `services/project_onboarding_scan.py`
# and `routers/fix_pipeline.py::_guardian_rescan` (a blocking network
# call there would stall the event loop). So the live lookup happens
# as a separate async step (`resolve_live_cves`, awaited explicitly by
# callers that are safely async) that only ever WRITES to this cache;
# `_scan_dependencies` only ever READS it — a plain dict get/set is
# safe to share across the to_thread() worker thread and the event
# loop under the GIL for this simple single-key-at-a-time access
# pattern.
_OSV_CACHE: dict[str, dict] = {}
_OSV_CACHE_TTL_S = 24 * 60 * 60  # 24h — matches the ticket's cache design
OSV_TOTAL_TIMEOUT_S = 15.0  # review fix #1 — hard overall deadline for the
# ENTIRE resolve_live_cves call (Mongo cold-start read + OSV querybatch +
# bounded detail fan-out), not just the OSV leg. routers/codebase_health.py
# already had a real Cloudflare 520 incident; a hung third party must never
# make the scan endpoint hang with it — on timeout we log and fall back to
# the static map exactly like any other failure (same existing contract).
_cold_start_loaded = False  # set True after the first Mongo cache restore
# attempt per process (success OR failure) — review fix (b): read the
# persisted cache back on cold start instead of writing to Mongo and never
# reading it. Guarded so a restart only probes Mongo once, not on every scan.


def _extract_declared_packages(
    text_cache: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """Pure parsing step, shared by `_scan_dependencies` (static +
    live-cache read) and `resolve_live_cves` (live-query build list) —
    one parser, not two, per AGENTS.md "reuse over reinvent".

    Returns (resolved, unresolved):
      resolved:   [{"path","line","pkg","ver","ecosystem","exact_pin","range_note"}]
      unresolved: [{"path","line","pkg"}] — a real dependency line with
                  NO usable version (bare `requests`, npm `"workspace:*"`,
                  `"latest"`, etc.). Ticket B requires these to surface
                  as an explicit "skipped, couldn't resolve" finding
                  instead of silently falling through the regex, the
                  original behaviour before this ticket.
    """
    resolved: list[dict] = []
    unresolved: list[dict] = []
    for path, text in text_cache.items():
        if path.endswith("requirements.txt") or path.endswith("requirements-dev.txt"):
            for i, line in enumerate((text or "").splitlines(), start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                    continue  # blank/comment/-e/-r/--index-url — not a package line
                m = re.match(
                    r'^([A-Za-z0-9_\-]+)(\[[A-Za-z0-9_,\-]+\])?\s*([=<>!~]+)\s*([0-9][0-9A-Za-z.\-]*)',
                    stripped,
                )
                if m:
                    pkg, _extras, op, ver = m.group(1).lower(), m.group(2), m.group(3), m.group(4)
                    resolved.append({
                        "path": path, "line": i, "pkg": pkg, "ver": ver,
                        "ecosystem": "PyPI", "exact_pin": (op == "=="),
                        "range_note": None,
                    })
                    continue
                m2 = re.match(r'^([A-Za-z0-9_\-]+)(\[[A-Za-z0-9_,\-]+\])?\s*$', stripped)
                if m2:
                    unresolved.append({"path": path, "line": i, "pkg": m2.group(1).lower()})
        if path.endswith("package.json"):
            try:
                pkg_json = json.loads(text or "{}")
                deps = {**(pkg_json.get("dependencies") or {}),
                        **(pkg_json.get("devDependencies") or {})}
                for pkg, ver_raw in deps.items():
                    ver_raw_s = str(ver_raw)
                    ver = re.sub(r'^[^\d]*', '', ver_raw_s)
                    if not ver or not ver[0].isdigit():
                        unresolved.append({"path": path, "line": 0, "pkg": pkg.lower()})
                        continue
                    range_note = (
                        "declared range, base version checked"
                        if ver != ver_raw_s else None
                    )
                    resolved.append({
                        "path": path, "line": 0, "pkg": pkg.lower(), "ver": ver,
                        "ecosystem": "npm", "exact_pin": True,
                        "range_note": range_note,
                    })
            except Exception:
                pass
    return resolved, unresolved


async def resolve_live_cves(text_cache: dict[str, str]) -> None:
    """Public entry point — wraps `_resolve_live_cves_inner` with a hard
    15s total time budget (review fix #1). On timeout OR any other
    failure (network error, 5xx, breaker-open) this logs and returns —
    it NEVER raises, and the cache is simply left as it was, so
    `_scan_dependencies` falls back to the static `_CVE_DB` for every
    package that was never successfully warmed. The scan result is
    never worse than it was before this ticket, and the caller
    (the /scan endpoint, onboarding's auto-scan) is never blocked
    beyond `OSV_TOTAL_TIMEOUT_S` regardless of what OSV or Mongo does.
    """
    try:
        await asyncio.wait_for(_resolve_live_cves_inner(text_cache), timeout=OSV_TOTAL_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning(
            "[osv] resolve_live_cves exceeded the %.0fs total time budget — "
            "static map stays authoritative for this scan", OSV_TOTAL_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning("[osv] live CVE lookup failed, static map stays authoritative: %s", e)


async def _resolve_live_cves_inner(text_cache: dict[str, str]) -> None:
    """Call this BEFORE `_scan_dependencies` from any already-async call
    site (the router's /scan endpoint and onboarding's auto-scan — NOT
    `_guardian_rescan`'s per-file verify, which must stay fast/network-
    free per the ticket). Never called directly by scan callers — see
    `resolve_live_cves` above for the timeout/error boundary.

    Only queries OSV for `exact_pin` entries (requirements.txt `==`
    pins, and every package.json entry since npm ranges are checked at
    their stripped base version with a disclaimer note — see
    `_extract_declared_packages`). A requirements.txt range pin
    (`>=`, `~=`, ...) is intentionally left OUT of the live query; it
    still gets the static `_CVE_DB` fallback check inside
    `_scan_dependencies`, exactly as before this ticket.

    A cache entry marked `incomplete` (review fix #2 — at least one of
    its vulns had a failed/capped detail fetch) is re-queried here
    regardless of its TTL age, so the missing severity gets retried on
    the very next scan rather than being stuck for 24h.
    """
    await _load_cache_from_mongo_once()
    resolved, _unresolved = _extract_declared_packages(text_cache)
    now = time.time()
    to_query: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for item in resolved:
        if not item["exact_pin"]:
            continue
        key = f"{item['ecosystem'].lower()}:{item['pkg']}:{item['ver']}"
        if key in seen:
            continue
        seen.add(key)
        cached = _OSV_CACHE.get(key)
        if (cached is not None and not cached.get("incomplete")
                and (now - cached["checked_at"]) < _OSV_CACHE_TTL_S):
            continue
        to_query.append((item["ecosystem"], item["pkg"], item["ver"]))
    if not to_query:
        return

    from services import osv_client  # local import avoids a module-load-time
                                      # dependency cycle risk; this module is
                                      # imported very early (router boundary
                                      # fix, see file docstring).
    results = await osv_client.query_osv_batch(to_query)  # may raise
    # ExternalCallError — caught by the outer resolve_live_cves's wait_for
    # boundary along with asyncio.TimeoutError, both treated identically.

    for key, vulns in results.items():
        incomplete = any(v.get("fetch_ok") is False for v in vulns)
        _OSV_CACHE[key] = {"vulns": vulns, "checked_at": now, "incomplete": incomplete}
    await _persist_cache(results, now)


async def _load_cache_from_mongo_once() -> None:
    """Restore the in-process OSV cache from Mongo on the first call per
    process (review fix (b)) — otherwise `_persist_cache`'s write is
    write-only and every fresh worker/restart re-hits OSV for packages
    a sibling process (or this one, pre-restart) already checked
    minutes ago. Runs at most once per process; best-effort, same
    non-fatal failure contract as `_persist_cache` — a Mongo read
    failure here must not block the live lookup, it just means this
    process starts with a cold cache, identical to today's behaviour.
    Respects the existing TTL (only restores entries still fresh) and
    the existing upsert-by-`_id` design — no new index, this is a
    cold-start-only read, not a hot path."""
    global _cold_start_loaded
    if _cold_start_loaded:
        return
    _cold_start_loaded = True  # set first — never retry-storm Mongo on repeated failures
    try:
        from cto_services.db import get_db
        db = get_db()
        now = time.time()
        cutoff_iso = datetime.fromtimestamp(now - _OSV_CACHE_TTL_S, tz=timezone.utc).isoformat()
        cursor = db.osv_cve_cache.find({"checked_at": {"$gte": cutoff_iso}})
        async for doc in cursor:
            checked_dt = datetime.fromisoformat(doc["checked_at"])
            _OSV_CACHE[doc["_id"]] = {
                "vulns": doc.get("vulns") or [],
                "checked_at": checked_dt.timestamp(),
                "incomplete": bool(doc.get("incomplete")),
            }
    except Exception as e:
        logger.warning("[osv] cold-start cache restore failed (non-fatal, starting cold): %s", e)


async def _persist_cache(results: dict[str, list[dict]], checked_at: float) -> None:
    """Best-effort Mongo persist — upsert by deterministic `_id`
    (`"<ecosystem>:<pkg>:<ver>"`), so re-running this is always
    idempotent and no new index is needed (ticket's explicit cache
    design). Never raises: a persist failure must never fail the scan
    (ticket requirement) — the in-process `_OSV_CACHE` dict already has
    the data for this run either way."""
    try:
        from cto_services.db import get_db
        db = get_db()
        checked_iso = datetime.fromtimestamp(checked_at, tz=timezone.utc).isoformat()
        for key, vulns in results.items():
            eco, pkg, ver = key.split(":", 2)
            incomplete = any(v.get("fetch_ok") is False for v in vulns)
            await db.osv_cve_cache.update_one(
                {"_id": key},
                {"$set": {
                    "ecosystem": eco, "package": pkg, "version": ver,
                    "vulns": vulns, "checked_at": checked_iso,
                    "incomplete": incomplete,
                }},
                upsert=True,
            )
    except Exception as e:
        logger.warning("[osv] cache persist failed (non-fatal, scan unaffected): %s", e)


_SEV_RANK = {"critical": 0, "high": 1, "unknown": 2, "medium": 3, "low": 4, "info": 5}
_MAX_VULNS_PER_FINDING = 5


def _scan_dependencies(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    resolved, unresolved = _extract_declared_packages(text_cache)

    for item in unresolved:
        out.append(_dep_unresolved_finding(item["path"], item["line"], item["pkg"]))

    for item in resolved:
        path, line, pkg, ver = item["path"], item["line"], item["pkg"], item["ver"]
        key = f"{item['ecosystem'].lower()}:{pkg}:{ver}"
        cached = _OSV_CACHE.get(key)
        age_ok = cached is not None and (time.time() - cached["checked_at"]) < _OSV_CACHE_TTL_S
        if cached is not None and (age_ok or cached.get("incomplete")):
            # Live result is authoritative whenever we have one at all —
            # either still fresh, OR "incomplete" (review fix #2: real
            # OSV ids exist for this package, a few just have no
            # severity yet because a detail fetch failed/was capped;
            # `resolve_live_cves` retries those regardless of TTL age
            # on the next scan). An authoritative empty list ("OSV
            # checked this and found nothing") intentionally does NOT
            # fall through to the static map either way.
            if cached["vulns"]:
                out.append(_dep_osv_finding(path, line, pkg, ver, cached["vulns"], item["range_note"]))
            continue
        # Never queried yet (not an exact pin, or not warmed this
        # process) → static fallback, first-match, exactly as before
        # this ticket.
        for prefix, cve, sev in _CVE_DB.get(pkg, []):
            if ver.startswith(prefix):
                out.append(_dep_finding(path, line, pkg, ver, cve, sev))
                break
    return out


def _dep_unresolved_finding(path: str, line: int, pkg: str) -> dict:
    return {
        "id":         f"dep::{path}:{line}:{pkg}:unresolved",
        "category":   "dependencies",
        "severity":   _norm_sev("INFO"),
        "file":       path, "line": line,
        "title":      f"version not resolvable: {pkg}",
        "message":    f"{pkg} has no pinned/parseable version — skipped the "
                       "vulnerability check (no version to compare).",
        "fix_hint":   f"Pin `{pkg}` to a specific version so it can be checked "
                       "for known vulnerabilities.",
        "fix_tokens": 0,
    }


def _dep_sev_label(raw: Optional[str]) -> str:
    """Per-vulnerability severity label for live OSV results. Unlike
    `_norm_sev` (used everywhere else in this file and defaults
    unrecognized input to "medium"), a missing/unresolved severity here
    must render as "unknown" rather than be silently promoted to
    "medium" — review fix #2. `None` (fetch failed/capped, will be
    retried next scan) and the literal string "unknown" (OSV reached
    successfully, genuinely has no severity data, e.g. real advisory
    PYSEC-2023-74) both render identically to the user; only the
    cache's `incomplete` flag (see `_resolve_live_cves_inner`)
    distinguishes them internally for retry purposes."""
    if raw and raw.lower() in {"critical", "high", "medium", "low"}:
        return raw.lower()
    return "unknown"


def _dep_osv_finding(path: str, line: int, pkg: str, ver: str,
                      vulns: list[dict], range_note: Optional[str]) -> dict:
    ranked = sorted(vulns, key=lambda v: _SEV_RANK.get(_dep_sev_label(v.get("severity")), 2))
    top = ranked[:_MAX_VULNS_PER_FINDING]
    worst_sev = _dep_sev_label(top[0].get("severity")) if top else "unknown"
    ids = ", ".join(f"{v['id']} ({_dep_sev_label(v.get('severity'))})" for v in top)
    more = f" (+{len(ranked) - _MAX_VULNS_PER_FINDING} more)" if len(ranked) > _MAX_VULNS_PER_FINDING else ""
    note = f" [{range_note}]" if range_note else ""
    return {
        "id":         f"dep::{path}:{line}:{pkg}:osv",
        "category":   "dependencies",
        "severity":   worst_sev,
        "file":       path, "line": line,
        "title":      f"vulnerable: {pkg}=={ver} ({len(ranked)} known vuln"
                       f"{'s' if len(ranked) != 1 else ''})",
        "message":    f"{pkg}=={ver}{note} → {ids}{more} — upgrade required.",
        "fix_hint":   f"Bump `{pkg}` past the version fixed in {top[0]['id']}"
                       + (" and the other CVEs listed above." if len(top) > 1 else "."),
        "fix_tokens": 5,
    }


def _dep_finding(path: str, line: int, pkg: str,
                 ver: str, cve: str, sev: str) -> dict:
    msg_extra = (
        f"{cve} — upgrade required."
        if cve.startswith("CVE-")
        else "This version is past EOL and missing security patches."
    )
    return {
        "id":         f"dep::{path}:{line}:{pkg}",
        "category":   "dependencies",
        "severity":   _norm_sev(sev),
        "file":       path, "line": line,
        "title":      f"vulnerable: {pkg}=={ver}",
        "message":    f"{pkg}=={ver} → {msg_extra}",
        "fix_hint":   f"Bump `{pkg}` to the latest stable release.",
        "fix_tokens": 5,
    }


# ──────────────────────────────────────────────────────────────────────
# Category 5 — Database
# ──────────────────────────────────────────────────────────────────────
def _scan_database(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        if not path.endswith(".py") or not text:
            continue
        # 1. AsyncIOMotorClient without pool config
        for m in re.finditer(r'AsyncIOMotorClient\([^)]*\)', text):
            inner = m.group(0)
            line = text[:m.start()].count("\n") + 1
            if ("maxPoolSize" not in inner) or ("maxIdleTimeMS" not in inner):
                out.append({
                    "id":       f"db::{path}:{line}:no_pool",
                    "category": "database",
                    "severity": _norm_sev("HIGH"),
                    "file":     path, "line": line,
                    "title":    "no_connection_pool",
                    "message":  "Motor client missing pool config — starves under traffic.",
                    "fix_hint": "Add maxPoolSize=50, minPoolSize=5, maxIdleTimeMS=30_000, connectTimeoutMS=10_000.",
                    "fix_tokens": 5,
                })
        # 2. Hard caps >2000 with no pagination
        for m in re.finditer(r'\.to_list\((\d{4,})\)', text):
            cap = int(m.group(1))
            if cap >= 2000:
                line = text[:m.start()].count("\n") + 1
                out.append({
                    "id":       f"db::{path}:{line}:hard_cap_{cap}",
                    "category": "database",
                    "severity": _norm_sev("MEDIUM"),
                    "file":     path, "line": line,
                    "title":    f"hard_cap_{cap}",
                    "message":  f"Loading {cap} documents at once — one request can kill the DB.",
                    "fix_hint": "Accept ?page=&limit= query params instead.",
                    "fix_tokens": 5,
                })
        # 3. No TTL on session/log/etc indices — detect collections
        # mentioned without an `expireAfterSeconds`. Best-effort heuristic.
        for m in re.finditer(r'await\s+db\.(\w*(?:session|log|temp|cache)\w*)\.insert', text):
            coll = m.group(1)
            line = text[:m.start()].count("\n") + 1
            # only flag once per file+collection
            key = f"db::{path}:0:no_ttl_{coll}"
            if any(o["id"] == key for o in out):
                continue
            out.append({
                "id":       key,
                "category": "database",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": line,
                "title":    f"no_ttl_{coll}",
                "message":  f"`{coll}` writes detected — confirm a TTL index exists.",
                "fix_hint": f'Add `await db.{coll}.create_index("created_at", expireAfterSeconds=86400)`.',
                "fix_tokens": 5,
            })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 6 — Docker CIS Benchmark
# Iter 212m-190 — Rule bodies extracted to
# `services/full_scan_scanners.py` so Loop-Mode Full Scan can call
# them without importing the router layer. This wrapper preserves
# the historical `_scan_docker_cis(text_cache)` call site.
# ──────────────────────────────────────────────────────────────────────
def _scan_docker_cis(text_cache: dict[str, str]) -> list[dict]:
    return _scan_docker_cis_service(text_cache)


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────
def _norm_sev(sev: str) -> str:
    s = (sev or "").upper()
    if s == "WARNING":
        return "medium"
    return s.lower() if s in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"} else "medium"


SEV_WEIGHTS = {
    "critical": 25, "high": 8,
    # Review fix #2 — a live OSV vuln with unresolved severity is a
    # REAL, confirmed vulnerability (a real CVE/GHSA id), just one
    # whose severity isn't known yet. It must score as a genuine risk,
    # not vanish like an "info" note (weight 0) — weighted between
    # high and medium until severity resolves (next scan, once the
    # retried detail fetch succeeds or OSV itself confirms "no data").
    "unknown": 5,
    "medium": 3, "low": 1, "info": 0,
}


def _score_for_findings(findings: list[dict]) -> int:
    """0-100 health score using a diminishing-returns curve.

    Iter 212m-164 — replaced the old linear `100 - sum(weights)` formula
    which cliff-edged at 4 criticals (4 × 25 = 100 → score 0).  Real
    repos routinely surface 5-15 critical findings on first scan and
    the founder needs to see the score MOVE as they fix issues, not
    sit stuck at 0.

    New curve:  score = round(100 · exp(-raw / 60))

    Reference points:
        0  issues   → score 100  (HEALTHY)
        5  medium   → raw 15  → score 78  (GOOD)
        5  high     → raw 40  → score 51  (NEEDS ATTENTION)
        2  critical → raw 50  → score 44  (NEEDS ATTENTION)
        4  critical → raw 100 → score 19  (CRITICAL RISK)
        9  critical → raw 225 → score 2   (CRITICAL RISK)

    The curve preserves severity ordering (criticals still dominate)
    but every fix produces a visible score delta, which is the whole
    point of a health gauge during pre-launch.
    """
    raw = sum(SEV_WEIGHTS.get(f.get("severity") or "low", 0) for f in findings)
    if raw <= 0:
        return 100
    import math
    return max(0, min(100, round(100 * math.exp(-raw / 60))))


def _category_label(score: int) -> tuple[str, str]:
    # Iter 212m-164 — thresholds re-tuned for the diminishing-returns
    # curve so the bands still split the score space roughly into
    # quarters under the new compression.
    if score <  20:
        return ("CRITICAL RISK",     "critical")
    if score <  50:
        return ("NEEDS ATTENTION",   "warn")
    if score <= 80:
        return ("GOOD",              "good")
    return                  ("HEALTHY",           "healthy")


SCANNERS = {
    "security":     _scan_security,
    "performance":  _scan_performance,
    "code_quality": _scan_code_quality,
    "dependencies": _scan_dependencies,
    "database":     _scan_database,
    "bug_hunt":     scan_bug_hunt,
    "docker":       _scan_docker_cis,
}

# 2026-10 — "Launch-ready X / 16" scorecard: 9 additional categories
# researched against real roadmap.sh guides (Vibe Coding, AI Product
# Builder, API Security Best Practices) — see services/
# codebase_health_extra.py module docstring and /app/memory/PRD.md for
# the source mapping. Same pure-function `(text_cache) -> list[finding]`
# contract as every scanner above, so they slot into every existing
# endpoint (scan/fix/score) with zero special-casing.
from services.codebase_health_extra import EXTRA_SCANNERS  # noqa: E402
SCANNERS.update(EXTRA_SCANNERS)
