"""
services/checkup_checks_runs.py — "does it run?" checks: unresolved
imports, undeclared packages, missing routes, dead buttons.  Pure
functions over the `{path: content}` text cache; see checkup_common.
"""
from __future__ import annotations

import json
import re

from services.checkup_meta import strip_meta
from services.checkup_common import _JS_EXT, _files, _is_client, _item, _line_of

_NODE_BUILTINS = {
    "fs", "path", "os", "http", "https", "url", "util", "crypto", "stream", "events",
    "child_process", "zlib", "buffer", "assert", "net", "tls", "dns", "readline",
    "querystring", "timers", "vm", "worker_threads", "cluster", "module", "process",
    "perf_hooks", "string_decoder", "constants", "v8", "async_hooks",
}
_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"\n;]*?\sfrom\s+)?|export\s+[^'"\n;]*?\sfrom\s+|require\(\s*|import\(\s*)["']([^"']+)["']""")
_ASSET_RE = re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|woff2?|ttf|mp4|css|scss)$")
_RESOLVE_EXT = _JS_EXT + (".json", ".css", ".scss", ".svg", ".png", ".jpg")
_NON_PACKAGE_PREFIXES = (".", "/", "node:", "http", "@/", "~/", "#")


def _normalize(from_path: str, spec: str) -> str:
    base = from_path.rsplit("/", 1)[0] if "/" in from_path else ""
    parts = (base + "/" + spec).split("/") if base else spec.split("/")
    norm: list[str] = []
    for seg in parts:
        if seg == "..":
            if norm:
                norm.pop()
        elif seg not in ("", "."):
            norm.append(seg)
    return "/".join(norm)


def _resolve_relative(tc_paths: set[str], from_path: str, spec: str) -> bool:
    target = _normalize(from_path, spec)
    if target in tc_paths:
        return True
    if any(target + ext in tc_paths for ext in _RESOLVE_EXT):
        return True
    return any(target + "/index" + ext in tc_paths for ext in _JS_EXT)


def _unresolved_in_file(paths: set[str], path: str, text: str) -> list[dict]:
    out = []
    for m in _IMPORT_RE.finditer(text):
        spec = m.group(1)
        # assets (e.g. ./logo.png) may be filtered from the text cache
        if not spec.startswith(".") or _ASSET_RE.search(spec):
            continue
        if _resolve_relative(paths, path.lstrip("/"), spec):
            continue
        out.append(_item(
            scanner="bug_hunt", rule_id="unresolved_import", severity="high",
            title="A page points to a file that doesn't exist",
            impact="The app will fail to build or show a blank screen where this part should be.",
            file=path, line=_line_of(text, m.start()),
            detail=f"import of missing file: {spec}",
            fix_hint="Create the file or fix the path."))
    return out


def check_unresolved_imports(tc: dict[str, str]) -> list[dict]:
    paths = {p.lstrip("/") for p in strip_meta(tc)}
    out: list[dict] = []
    for path, text in _files(tc):
        if path.endswith(_JS_EXT):
            out.extend(_unresolved_in_file(paths, path, text))
    return out


_ALL_DEP_KEYS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def _package_roots(tc: dict[str, str]) -> dict[str, set[str]]:
    """{directory of each package.json: every dependency name it declares}."""
    pkgs: dict[str, set[str]] = {}
    for p, t in _files(tc):
        if not p.endswith("package.json"):
            continue
        try:
            j = json.loads(t)
        except (ValueError, TypeError):
            continue
        declared: set[str] = set()
        for key in _ALL_DEP_KEYS:
            declared |= set(j.get(key) or {})
        pkgs[p.lstrip("/").rsplit("package.json", 1)[0]] = declared
    return pkgs


def _package_name(spec: str) -> str:
    return "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]


def _is_known_package(name: str, declared: set[str]) -> bool:
    return name in declared or name in _NODE_BUILTINS or name.startswith("virtual:")


def _undeclared_in_file(path: str, text: str, declared: set[str]) -> list[dict]:
    out = []
    for m in _IMPORT_RE.finditer(text):
        spec = m.group(1)
        if spec.startswith(_NON_PACKAGE_PREFIXES):
            continue
        name = _package_name(spec)
        if _is_known_package(name, declared):
            continue
        out.append(_item(
            scanner="bug_hunt", rule_id="undeclared_package", severity="high",
            title="The app uses a tool it never installs",
            impact="It may work on one computer and then crash when it's launched.",
            file=path, line=_line_of(text, m.start()),
            detail=f"package not in package.json: {name}",
            confidence="medium",
            fix_hint="Add it to the project's dependencies."))
    return out


def _dedupe_by_detail(items: list[dict]) -> list[dict]:
    """One item per package per project is enough."""
    seen: set[str] = set()
    uniq = []
    for i in items:
        k = i["evidence"]["detail"]
        if k not in seen:
            seen.add(k)
            uniq.append(i)
    return uniq


def check_undeclared_packages(tc: dict[str, str]) -> list[dict]:
    pkgs = _package_roots(tc)
    if not pkgs:
        return []
    out: list[dict] = []
    for path, text in _files(tc):
        if not path.endswith(_JS_EXT):
            continue
        p = path.lstrip("/")
        root = max((r for r in pkgs if p.startswith(r)), key=len, default=None)
        if root is not None:
            out.extend(_undeclared_in_file(path, text, pkgs[root]))
    return _dedupe_by_detail(out)


_EMPTY_HANDLER = re.compile(r"onClick=\{\s*(?:\(\s*\)\s*=>\s*\{\s*\}|undefined|null|\(\)\s*=>\s*null)\s*\}")


def check_dead_buttons(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        if not path.endswith((".jsx", ".tsx")):
            continue
        for m in _EMPTY_HANDLER.finditer(text):
            out.append(_item(
                scanner="bug_hunt", rule_id="dead_button", severity="medium",
                title="A button does nothing when clicked",
                impact="Visitors will tap it and think the app is broken.",
                file=path, line=_line_of(text, m.start()),
                detail="click handler is empty", confidence="medium"))
    return out


_FETCH_RE = re.compile(r"""fetch\(\s*[`"'](/api/[A-Za-z0-9_\-/]+)[`"']""")
_ROUTE_LINE = re.compile(r"(?:\.(?:get|post|put|patch|delete|route|use)\(|@\w+\.(?:get|post|put|patch|delete)\(|route\()")


def _server_route_text(tc: dict[str, str]) -> str:
    return "\n".join(
        ln for p, t in _files(tc) if not _is_client(p) or "/api/" in "/" + p
        for ln in t.splitlines() if _ROUTE_LINE.search(ln))


def _route_defined(route: str, paths: list[str], server_text: str) -> bool:
    tail = route[len("/api"):]
    prefixes = ("pages" + route, "app" + route, "src/pages" + route, "src/app" + route, "api" + tail)
    if any(p.startswith(prefixes) for p in paths):
        return True
    return route in server_text or tail in server_text


def check_missing_routes(tc: dict[str, str]) -> list[dict]:
    server_text = _server_route_text(tc)
    paths = [p.lstrip("/") for p in strip_meta(tc)]
    out = []
    for path, text in _files(tc):
        if not path.endswith(_JS_EXT) or not _is_client(path):
            continue
        for m in _FETCH_RE.finditer(text):
            route = m.group(1).rstrip("/")
            if _route_defined(route, paths, server_text):
                continue
            out.append(_item(
                scanner="bug_hunt", rule_id="missing_route", severity="high",
                title="A screen asks the server for something that isn't there",
                impact="That part of the app will show an error or stay empty.",
                file=path, line=_line_of(text, m.start()),
                detail=f"no server route found for {route}", confidence="medium"))
    return out
