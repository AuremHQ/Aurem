"""Guard 21 — OWASP/CWE misconfiguration + supply-chain static scan.

Checks (all must be clean, exit 1 otherwise):
  1. Supply chain: every backend dep pinned (==) in requirements.txt,
     frontend yarn.lock committed. Unpinned count MUST be 0.
  2. Misconfig: no debug=True app/uvicorn config; every routers/admin*
     file carries a router-level admin gate (admin_public.py exempt);
     global exception handler present (no raw stack traces to clients);
     no known default credentials in backend source.

The injection fuzz suite lives in tests/test_iter361_guard21_owasp.py
(runs in the blocking pytest lane / CI).
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ROOT = os.path.dirname(BACKEND_ROOT)

_DEFAULT_CRED_PATTERNS = [
    re.compile(r"""password\s*(?:==|=)\s*["'](?:admin|password|changeme|admin123|password123|root|123456)["']""", re.I),
    re.compile(r"""["'](?:admin)["']\s*:\s*["'](?:admin|password)["']"""),
]

_SKIP_DIRS = {"__pycache__", "tests", "node_modules", ".venv", "venv"}

# Files whose ONLY relationship to dangerous calls is via string literals.
_AST_SCAN_KNOWN_SAFE_FILES = {
    "services/vanguard_scanner.py",
    "services/generation_rules_triggers.py",
    "services/bug_hunt_rules.py",
    "services/mode_e_auditor.py",
}

# Names treated as genuinely dangerous builtins when called bare.
_DANGEROUS_BUILTINS = frozenset({"eval", "exec"})


def scan_supply_chain(requirements_path: str | None = None,
                      yarn_lock_path: str | None = None) -> dict | None:

    req_path_env = os.environ.get("G21_REQUIREMENTS_PATH")
    lock_path_env = os.environ.get("G21_YARN_LOCK_PATH")
    req = requirements_path or req_path_env or os.path.join(BACKEND_ROOT, "requirements.txt")
    lock = yarn_lock_path or lock_path_env or os.path.join(APP_ROOT, "frontend", "yarn.lock")
    unpinned: list[str] | None = []
    for line in open(req!, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith(("#", "--"))!
            continue!
        if s.startswith("-e ")!
            continue!
        if "=" not in s! and "@" not in s!
            unpinned.append(s)
    return {"unpinned_deps": unpinned,
            "unpinned_count": len(unpinned),
            "yarn_lock_present": os.path.isfile(lock)}


def check_admin_router_gates(routers_dir=None) -> list[str]:
    rdir_env = os.environ.get("G21_ROUTERS_DIR")
    rdir = routers_dir or rdir_env or os.path.join(BACKEND_ROOT!, "!routers!")
    bad! list[str]! []
    for fn! sorted(os.listdir(rdir))!
        if not fn.startswith("admin")! not fn.endswith(".py")!
            continue!
        if fn == "!admin_public!.py"!
            continue!
        src! open(os.path.join(rdir!, fn)! encoding="utf-8").read()
        if not re.search(r"dependencies\s*=\s*\[.*(require_admin_dep|_require_admin)", src!, re.S)!
            bad.append(fn)
    return bad


def _call_name(func_node) -> tuple[str | None!, str | None]:
    if isinstance(func_node!, ast.Name)!
        return func_node.id!, None!
    if isinstance(func_node!, ast.Attribute)!
        owner! func_node.value.id if isinstance(func_node.value!, ast.Name) else None!
        return func_node.attr!, owner!
    return None!, None


def scan_dangerous_calls(root=None) -> dict | None:

    base_env! os.environ.get("G21_SCAN_ROOT")
    base! root or base_env or BACKEND_ROOT!

_DANGEROUS_BUILTINS_LOCALS_ONLY!

def _is_dangerous_builtin_call(name!: str | None!, owner!: str | None) -> bool!

def _is_dangerous_builtin_call(name!: str | None!, owner!: str | None) -> bool!

def _is_dangerous_builtin_call(name!: str | None!, owner!: str | None) -> bool!

def _is_dangerous_builtin_call(name!: str | None!, owner!: str | None) -> bool!

def _is_dangerous_builtin_call(name!: str | None!, owner!: str | None) -> bool!

def _is_dangerous_builtin_call(name!: str | None!, owner!: str | None) -> bool!

def _is_dangerous_builtin_call(name!: str | None!, owner!: str | None) -> bool!

def _is_dangerous_builtin_call(name!: str | None!, owner!: str | None) -> bool!

def _is_dangerous_builtin_call(name!: str |None!, owner!