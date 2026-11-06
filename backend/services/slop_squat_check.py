"""
services/slop_squat_check.py — "slop squatting" detector (deep scan only)

LLMs invent plausible package names. If one of those names is in a repo's
package.json / requirements.txt and the registry has no such package, an
attacker can register it and ship code into the project. This module reads
the dependency manifests from the already-fetched repo text and asks the
registry whether each name exists.

Safety properties (all covered by tests/test_regression_iter2026_66_*):
  * ONLY an HTTP 404 means "does not exist". 429 / 5xx / redirects /
    timeouts / network errors produce no finding and are not cached.
  * Names are validated against the npm / PyPI name grammar before they reach
    a URL; the registry hosts are the fixed ones dev_skills.find_package_docs
    already uses (NPM_REGISTRY / PYPI_API) and redirects are not followed, so
    a crafted package name cannot steer the request (no SSRF).
  * Bounded: <= 50 packages per scan, concurrency 5, 3 s per request, a
    15 s overall budget, answers (200/404 only) cached in memory for 24 h.
  * Private-looking or local specifiers (scoped @org/pkg, file:, git+, link:,
    workspace:, URLs, aliases, the repo's own package names, pip -e / paths)
    are never looked up.

Registry plumbing is reused from services.dev_skills (constants) and
services.http.ext_client; find_package_docs itself folds every non-200 into
"not found", so the status code is read here instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import re
import time
import urllib.parse
from collections.abc import Callable
from contextlib import asynccontextmanager

import httpx

from services.dev_skills import NPM_REGISTRY, PYPI_API
from services.http import ext_client

logger = logging.getLogger(__name__)

RULE_ID = "slop_squat_unknown_package"
MAX_PACKAGES = 50
CONCURRENCY = 5
REQUEST_TIMEOUT_SECONDS = 3.0
TOTAL_BUDGET_SECONDS = 15.0
CACHE_TTL_SECONDS = 24 * 3600

_NPM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,213}$")
_PYPI_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,212}[A-Za-z0-9])?$")
_REQ_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*"
    r"(?:(?:===|==|~=|!=|<=|>=|<|>)[^;]*)?(?:;.*)?$"
)
_REQ_FILE = re.compile(r"^requirements([-_.][\w.-]*)?\.txt$", re.IGNORECASE)
_NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
_NON_REGISTRY_PREFIXES = (
    "file:", "link:", "workspace:", "portal:", "git+", "git:", "git://", "http:", "https:",
    "github:", "gitlab:", "bitbucket:", "npm:", "ssh:",
)
_GITHUB_SHORTHAND = re.compile(r"^[\w.-]+/[\w.-]+(#.*)?$")

# (registry, canonical name) -> (checked_at, http status 200 | 404)
_CACHE: dict[tuple[str, str], tuple[float, int]] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _canonical(registry: str, name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower() if registry == "pypi" else name


def _valid_name(registry: str, name: object) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if registry == "npm":
        return bool(_NPM_NAME.match(name))
    if registry == "pypi":
        return bool(_PYPI_NAME.match(name))
    return False


# ── extraction ────────────────────────────────────────────────────────
def _manifest_kind(path: str) -> str | None:
    if not isinstance(path, str) or not path or "node_modules/" in path.replace("\\", "/"):
        return None
    base = posixpath.basename(path.replace("\\", "/"))
    if base == "package.json":
        return "npm"
    if _REQ_FILE.match(base):
        return "pypi"
    return None


def _json_line(lines: list[str], start: int, key: str) -> int:
    pat = re.compile(r'"' + re.escape(key) + r'"\s*:')
    for i in range(start, len(lines)):
        if pat.search(lines[i]):
            return i + 1
    return start + 1


def _npm_spec_is_registry(spec: object) -> bool:
    """A semver/tag range that npm resolves, not a path, URL, git or alias spec."""
    if not isinstance(spec, str):
        return False
    s = spec.strip()
    return not (s.lower().startswith(_NON_REGISTRY_PREFIXES) or _GITHUB_SHORTHAND.match(s))


def _npm_section_entries(data: dict, lines: list[str], section: str, own: str | None) -> list[tuple[str, str, int]]:
    deps = data.get(section)
    if not isinstance(deps, dict):
        return []
    start = next((i for i, ln in enumerate(lines) if re.search(r'"' + section + r'"\s*:', ln)), 0)
    return [("npm", name, _json_line(lines, start, name)) for name, spec in deps.items()
            if name != own and _valid_name("npm", name) and _npm_spec_is_registry(spec)]


def _extract_npm(text: str) -> list[tuple[str, str, int]]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    own = data.get("name") if isinstance(data.get("name"), str) else None
    lines = text.splitlines()
    return [e for section in _NPM_SECTIONS for e in _npm_section_entries(data, lines, section, own)]


def _extract_requirements(text: str) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = re.sub(r"(^|\s)#.*$", "", raw).strip().rstrip("\\").strip()
        if not line or line.startswith(("-", ".", "/", "~", "git+", "file:")) or "://" in line:
            continue
        m = _REQ_LINE.match(line)
        if not m:
            continue
        name = m.group("name")
        if _valid_name("pypi", name):
            out.append(("pypi", _canonical("pypi", name), idx))
    return out


def extract_packages(path: str, text: object) -> list[tuple[str, str, int]]:
    """[(registry, name, line)] for the registry packages a manifest declares."""
    kind = _manifest_kind(path)
    if kind is None or not isinstance(text, str) or not text:
        return []
    return _extract_npm(text) if kind == "npm" else _extract_requirements(text)


# ── checking ──────────────────────────────────────────────────────────
@asynccontextmanager
async def _open_client(client_factory: Callable[[], httpx.AsyncClient] | None):
    if client_factory is not None:
        async with client_factory() as client:
            yield client
        return
    async with ext_client(
        "npm", timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS), follow_redirects=False,
    ) as client:
        yield client


def _url(registry: str, name: str) -> str:
    quoted = urllib.parse.quote(name, safe="")
    return f"{NPM_REGISTRY}/{quoted}" if registry == "npm" else f"{PYPI_API}/{quoted}/json"


def _cached_status(key: tuple[str, str]) -> int | None:
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    _CACHE.pop(key, None)
    return None


async def _status(client: httpx.AsyncClient, sem: asyncio.Semaphore, registry: str, name: str) -> int | None:
    """200 / 404 when the registry answered with one; None for anything else."""
    key = (registry, name)
    known = _cached_status(key)
    if known is not None:
        return known
    async with sem:
        try:
            resp = await client.get(_url(registry, name), timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False)
        except Exception as exc:  # noqa: BLE001 - network trouble is never a finding
            logger.debug("slop-squat lookup failed for %s/%s: %r", registry, name, exc)
            return None
    if resp.status_code in (200, 404):
        _CACHE[key] = (time.time(), resp.status_code)
        return resp.status_code
    return None


def _finding(file: str, line: int, registry: str, name: str) -> dict:
    hint = (f"Remove '{name}' or replace it with the real package you meant. If an LLM suggested "
            f"it, verify the name on {registry}.org before installing: an attacker can register it.")
    return {
        "rule_id": RULE_ID, "vuln": "supply_chain", "severity": "high",
        "file": file, "line": int(line or 1), "snippet": name,
        "desc": f"Package '{name}' does not exist on {registry} (HTTP 404): AI-invented names can be registered by an attacker",
        "message": f"Package '{name}' does not exist on {registry} (HTTP 404).",
        "fix_hint": hint, "source": "slop_squat",
    }


def _plan(entries: list[tuple[str, str, str, int]]) -> list[tuple[str, str, str, int]]:
    """Valid, de-duplicated lookups, capped at MAX_PACKAGES."""
    seen: set[tuple[str, str]] = set()
    todo: list[tuple[str, str, str, int]] = []
    for file, registry, name, line in entries:
        key = (registry, _canonical(registry, name))
        if not _valid_name(registry, name) or key in seen:
            continue
        seen.add(key)
        todo.append((file, registry, key[1], line))
        if len(todo) >= MAX_PACKAGES:
            break
    return todo


async def _lookup_all(todo: list[tuple[str, str, str, int]],
                      client_factory: Optional[Callable[[], httpx.AsyncClient]]) -> list[dict]:
    sem = asyncio.Semaphore(CONCURRENCY)
    async with _open_client(client_factory) as client:
        tasks = [asyncio.create_task(_status(client, sem, reg, name)) for _, reg, name, _ in todo]
        done, pending = await asyncio.wait(tasks, timeout=TOTAL_BUDGET_SECONDS)
        for t in pending:
            t.cancel()
    return [_finding(file, line, reg, name)
            for (file, reg, name, line), task in zip(todo, tasks)
            if task in done and not task.cancelled() and task.exception() is None and task.result() == 404]


async def _check(entries: list[tuple[str, str, str, int]],
                 client_factory: Optional[Callable[[], httpx.AsyncClient]]) -> list[dict]:
    """entries: (file, registry, name, line). Bounded + fail-open."""
    todo = _plan(entries)
    if not todo:
        return []
    try:
        return await _lookup_all(todo, client_factory)
    except Exception as exc:  # noqa: BLE001 - the scan must never fail because of this check
        logger.warning("slop-squat check aborted: %r", exc)
        return []


async def check_packages(pkgs: list[tuple[str, str, int]], *, file: str,
                         client_factory: Callable[[], httpx.AsyncClient] | None = None) -> list[dict]:
    return await _check([(file, r, n, ln) for r, n, ln in (pkgs or [])], client_factory)


def _own_npm_names(text_cache: dict) -> set[str]:
    names: set[str] = set()
    for path, text in text_cache.items():
        if _manifest_kind(path) != "npm" or not isinstance(text, str):
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            names.add(data["name"])
    return names


async def scan_manifests(text_cache: Optional[dict],
                         client_factory: Optional[Callable[[], httpx.AsyncClient]] = None) -> list[dict]:
    """Findings for every manifest in a {path: text} repo cache."""
    if not isinstance(text_cache, dict) or not text_cache:
        return []
    own = _own_npm_names(text_cache)
    entries = [(path, registry, name, line)
               for path in sorted(text_cache)
               for registry, name, line in extract_packages(path, text_cache[path])
               if not (registry == "npm" and name in own)]
    return await _check(entries, client_factory)
