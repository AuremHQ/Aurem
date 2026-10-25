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

# 2026-08 · Security-triage session — files whose ONLY relationship to
# eval()/exec()/os.system() is that a regex-pattern *string literal* or
# a rule-description *string* mentions the token (e.g. the pattern
# r"exec\s*\(" used to DETECT `exec(` in scanned repos). The AST scan
# below already ignores these correctly on its own (it walks real Call
# nodes, never string contents), so this allowlist exists purely for
# clarity/documentation — it is NOT load-bearing for the false-positive
# fix. Kept short and named so a reviewer can see at a glance which
# files were investigated and cleared during the audit.
_AST_SCAN_KNOWN_SAFE_FILES = {
    "services/vanguard_scanner.py",
    "services/generation_rules_triggers