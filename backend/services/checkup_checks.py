"""
services/checkup_checks.py — deterministic checkup checks (S12: no LLM
decides whether something is a problem).

Each check is a pure function `(text_cache) -> list[item]` over the
`{path: content}` map from `repo_text_cache.build_text_cache`.  Items are
in the `fix_items` input shape and carry plain-English copy (S10); raw
detail lives in `evidence`.  Checks prefer a missed issue over a false
alarm: a founder who is told "your app is unsafe" wrongly stops trusting
the verdict.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Callable, Iterator

_SKIP_DIRS = ("node_modules/", "vendor/", ".agent/", "dist/", "build/",
              "benchmarks/", ".next/", "coverage/", ".git/")
_JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_CLIENT_HINTS = ("src/", "app/", "pages/", "components/", "public/", "frontend/", "client/")
_SERVER_HINTS = ("api/", "server/", "backend/", "supabase/functions/", "functions/",
                 "scripts/", "migrations/")
_NODE_BUILTINS = {
    "fs", "path", "os", "http", "https", "url", "util", "crypto", "stream", "events",
    "child_process", "zlib", "buffer", "assert", "net", "tls", "dns", "readline",
    "querystring", "timers", "vm", "worker_threads", "cluster", "module", "process",
    "perf_hooks", "string_decoder", "constants", "v8", "async_hooks",
}


def _is_vendored(path: str) -> bool:
    p = "/" + path.lstrip("/")
    return any(("/" + d) in p for d in _SKIP_DIRS)


def _files(tc: dict[str, str]) -> Iterator[tuple[str, str]]:
    for path, text in tc.items():
        if isinstance(text, str) and not _is_vendored(path):
            yield path, text


def _is_client(path: str) -> bool:
    p = path.lstrip("/")
    if any(h in ("/" + p) for h in ["/" + s for s in _SERVER_HINTS]):
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


# ── stack detection (S9: honest about what we can/can't verify) ───────
def detect_stack(tc: dict[str, str]) -> dict:
    pkg_texts = [t for p, t in _files(tc) if p.endswith("package.json")]
    deps: set[str] = set()
    for t in pkg_texts:
        try:
            j = json.loads(t)
        except (ValueError, TypeError):
            continue
        for k in ("dependencies", "devDependencies"):
            deps |= set((j.get(k) or {}).keys())
    py = any(p.endswith(("requirements.txt", "pyproject.toml")) for p, _ in _files(tc))
    supabase = ("@supabase/supabase-js" in deps
                or any(p.lstrip("/").startswith("supabase/") or "/supabase/migrations/" in p
                       for p, _ in _files(tc)))
    frontend = next((n for n in ("next", "react", "vue", "svelte") if n in deps), None)
    return {
        "frontend": frontend,
        "backend_python": py,
        "supabase": supabase,
        "has_package_json": bool(pkg_texts),
        "label": " + ".join(x for x in (frontend, "supabase" if supabase else None,
                                         "python" if py else None) if x) or "unknown",
    }


# ── safe ──────────────────────────────────────────────────────────────
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.(eyJ[A-Za-z0-9_-]{8,})\.[A-Za-z0-9_-]{8,}")
_SR_ENV_RE = re.compile(r"(?:VITE|NEXT_PUBLIC|REACT_APP|PUBLIC)_[A-Z0-9_]*SERVICE_ROLE[A-Z0-9_]*")


def _jwt_role(payload_b64: str) -> str:
    try:
        pad = "=" * (-len(payload_b64) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload_b64 + pad)).get("role", ""))
    except (ValueError, TypeError):
        return ""


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


# ── runs ──────────────────────────────────────────────────────────────
_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"\n;]*?\sfrom\s+)?|export\s+[^'"\n;]*?\sfrom\s+|require\(\s*|import\(\s*)["']([^"']+)["']""")


def _resolve_relative(tc_paths: set[str], from_path: str, spec: str) -> bool:
    base = from_path.rsplit("/", 1)[0] if "/" in from_path else ""
    parts = (base + "/" + spec).split("/") if base else spec.split("/")
    norm: list[str] = []
    for seg in parts:
        if seg in ("", "."):
            continue
        if seg == "..":
            if norm:
                norm.pop()
            continue
        norm.append(seg)
    target = "/".join(norm)
    if target in tc_paths:
        return True
    for ext in _JS_EXT + (".json", ".css", ".scss", ".svg", ".png", ".jpg"):
        if target + ext in tc_paths:
            return True
    return any(target + "/index" + ext in tc_paths for ext in _JS_EXT)


def check_unresolved_imports(tc: dict[str, str]) -> list[dict]:
    paths = {p.lstrip("/") for p in tc}
    out = []
    for path, text in _files(tc):
        if not path.endswith(_JS_EXT):
            continue
        for m in _IMPORT_RE.finditer(text):
            spec = m.group(1)
            if not spec.startswith("."):
                continue
            # assets (e.g. ./logo.png) may be filtered from the text cache
            if re.search(r"\.(png|jpe?g|gif|webp|svg|ico|woff2?|ttf|mp4|css|scss)$", spec):
                continue
            if not _resolve_relative(paths, path.lstrip("/"), spec):
                out.append(_item(
                    scanner="bug_hunt", rule_id="unresolved_import", severity="high",
                    title="A page points to a file that doesn't exist",
                    impact="The app will fail to build or show a blank screen where this part should be.",
                    file=path, line=_line_of(text, m.start()),
                    detail=f"import of missing file: {spec}",
                    fix_hint="Create the file or fix the path."))
    return out


def check_undeclared_packages(tc: dict[str, str]) -> list[dict]:
    pkgs: dict[str, set[str]] = {}
    for p, t in _files(tc):
        if p.endswith("package.json"):
            try:
                j = json.loads(t)
            except (ValueError, TypeError):
                continue
            d = set((j.get("dependencies") or {})) | set((j.get("devDependencies") or {})) \
                | set((j.get("peerDependencies") or {})) | set((j.get("optionalDependencies") or {}))
            pkgs[p.lstrip("/").rsplit("package.json", 1)[0]] = d
    if not pkgs:
        return []
    out = []
    for path, text in _files(tc):
        if not path.endswith(_JS_EXT):
            continue
        p = path.lstrip("/")
        root = max((r for r in pkgs if p.startswith(r)), key=len, default=None)
        if root is None:
            continue
        declared = pkgs[root]
        for m in _IMPORT_RE.finditer(text):
            spec = m.group(1)
            if spec.startswith((".", "/", "node:", "http", "@/", "~/", "#")):
                continue
            name = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
            if name in declared or name in _NODE_BUILTINS or name.startswith("virtual:"):
                continue
            out.append(_item(
                scanner="bug_hunt", rule_id="undeclared_package", severity="high",
                title="The app uses a tool it never installs",
                impact="It may work on one computer and then crash when it's launched.",
                file=path, line=_line_of(text, m.start()),
                detail=f"package not in package.json: {name}",
                confidence="medium",
                fix_hint="Add it to the project's dependencies."))
    seen, uniq = set(), []
    for i in out:  # one item per package per project is enough
        k = i["evidence"]["detail"]
        if k not in seen:
            seen.add(k)
            uniq.append(i)
    return uniq


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


def check_missing_routes(tc: dict[str, str]) -> list[dict]:
    server_text = "\n".join(
        ln for p, t in _files(tc) if not _is_client(p) or "/api/" in "/" + p
        for ln in t.splitlines() if _ROUTE_LINE.search(ln))
    paths = [p.lstrip("/") for p in tc]
    out = []
    for path, text in _files(tc):
        if not path.endswith(_JS_EXT) or not _is_client(path):
            continue
        for m in _FETCH_RE.finditer(text):
            route = m.group(1).rstrip("/")
            tail = route[len("/api"):]
            file_defined = any(
                p.startswith(("pages" + route, "app" + route, "src/pages" + route, "src/app" + route,
                              "api" + tail)) for p in paths)
            if file_defined or route in server_text or tail in server_text:
                continue
            out.append(_item(
                scanner="bug_hunt", rule_id="missing_route", severity="high",
                title="A screen asks the server for something that isn't there",
                impact="That part of the app will show an error or stay empty.",
                file=path, line=_line_of(text, m.start()),
                detail=f"no server route found for {route}", confidence="medium"))
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
]
