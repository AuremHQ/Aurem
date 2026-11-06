#!/usr/bin/env python3
"""CI guardrail (security audit 4.5): fail if a production config turns DEBUG on.

Scans git-tracked production configuration (.env / .env.production, Dockerfiles,
docker-compose files, fly/render/railway/vercel/Procfile/systemd units) for a
DEBUG-style flag set to a truthy value. Guardrail only: it changes no runtime
behaviour. Example/sample/dev/test configs and starter templates under
templates/ are not production config and are skipped.

Usage: python backend/scripts/ci_check_no_debug_true.py [repo_root]
Exit 0 = clean, 1 = a production config enables DEBUG.
"""
from __future__ import annotations

import posixpath
import re
import subprocess
import sys
from pathlib import Path

_PROD_BASENAME = re.compile(
    r"^(?:\.env(?:\.production(?:\.[\w-]+)?)?|Dockerfile(?:\.[\w.-]+)?|docker-compose(?:\.[\w.-]+)?\.ya?ml|"
    r"compose(?:\.[\w.-]+)?\.ya?ml|fly\.toml|render\.ya?ml|railway\.json|Procfile|vercel\.json|netlify\.toml|"
    r"[\w.-]+\.service)$",
    re.IGNORECASE,
)
_SKIP_PARTS = ("/templates/", "/tests/", "/test/", "/node_modules/", "/fixtures/", "/examples/")
_SKIP_MARKERS = (".example", ".sample", ".local", ".development", ".dev", ".test", ".staging")
_DEBUG = re.compile(
    r"""(?<![A-Za-z0-9_])(?:APP_|FASTAPI_|FLASK_|DJANGO_|VITE_|REACT_APP_)?DEBUG["']?\s*(?:[:=]|:-|\s)\s*["']?(?:true|1|yes|on)["']?\s*,?\s*$""",
    re.IGNORECASE,
)


def is_production_config(path: str) -> bool:
    p = "/" + path.replace("\\", "/").lstrip("/")
    base = posixpath.basename(p)
    if any(part in p.lower() for part in _SKIP_PARTS):
        return False
    if any(m in base.lower() for m in _SKIP_MARKERS):
        return False
    return bool(_PROD_BASENAME.match(base))


def find_debug_lines(text: str) -> list[tuple[int, str]]:
    hits = []
    for no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", ";")):
            continue
        if _DEBUG.search(stripped):
            hits.append((no, stripped[:120]))
    return hits


def scan(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    problems = []
    for rel in out.splitlines():
        if not is_production_config(rel):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for no, line in find_debug_lines(text):
            problems.append(f"{rel}:{no}: {line}")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2]
    problems = scan(root)
    if problems:
        print("DEBUG is enabled in production configuration:")
        for p in problems:
            print("  " + p)
        print("::error::A production config sets DEBUG to a truthy value. Remove it or move it to a dev-only file.")
        return 1
    print("OK: no production config enables DEBUG")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
