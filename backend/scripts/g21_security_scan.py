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
import platform
import re
import socket
import sys
from functools import lru_cache
from typing import Any

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ROOT = os.path.dirname(BACKEND_ROOT)

# -------------------------------------------------------------------
# Detect dangerous *default credentials* hardcoded in Python files
# Only catches patterns that assign a known weak password; *not*
# safe patterns inside config wrappers like os.environ.get("DB_PASS","...")
_CRED_DEFAULTS_PTN = (
    r"""(?<!\.)password\s*[:=]\s*['"]("""
    r"""admin(?:\d+)?|password\d*|changeme|letmein|welcome|"""
    r"""toor|\d{3,8}|qwerty|p@ssw0rd)['"]"""
)


@lru_cache(maxsize=None)
def load_default_credentials_model(
    model_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lazy-load the fallback credential model.

    On first invocation the caller passes ``model_data`` directly.
    Subsequent calls (cache hit) always succeed without touching disk.
    """
    return model_data if model_data is not None else {}


def detect_default_credentials(
    file_path: str,
    source_code: list