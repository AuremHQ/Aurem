"""tests/test_regression_iter2026_71_ci_debug_guard.py

Security audit 4.5: CI guardrail that fails when a production config enables
DEBUG. Behavioural tests against throw-away git repos, plus a check that the
real repository is clean today.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_check_no_debug_true.py"
spec = importlib.util.spec_from_file_location("ci_check_no_debug_true", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.mark.parametrize("path,body", [
    (".env.production", "DEBUG=true\n"),
    ("backend/.env", "export DEBUG=1\n"),
    ("backend/Dockerfile", "FROM python:3.11\nENV DEBUG=true\n"),
    ("frontend/Dockerfile", "ENV VITE_DEBUG true\n"),
    ("infra/docker-compose.yml", "services:\n  api:\n    environment:\n      - DEBUG=true\n"),
    ("infra/docker-compose.prod.yml", "services:\n  api:\n    environment:\n      DEBUG: \"yes\"\n"),
    ("vercel.json", '{"env": {"DEBUG": "true"}}\n'),
    ("fly.toml", "[env]\n  APP_DEBUG = \"on\"\n"),
    ("Procfile", "web: DEBUG=1 uvicorn x:app\n"),
])
def test_truthy_debug_in_production_config_fails(tmp_path, path, body):
    root = _repo(tmp_path, {path: body})
    assert guard.main(["x", str(root)]) == 1


@pytest.mark.parametrize("path,body", [
    (".env.production", "DEBUG=false\n"),
    (".env.production", "DEBUG=0\nLOG_LEVEL=info\n"),
    (".env.production", "# DEBUG=true\n"),
    ("backend/Dockerfile", "ENV DEBUGGER_PORT=5678\nENV NODE_DEBUG_NATIVE=1\n"),
    (".env.example", "DEBUG=true\n"),
    (".env.development", "DEBUG=true\n"),
    ("backend/templates/stacks/x/docker-compose.yml", "environment:\n  - DEBUG=true\n"),
    ("backend/tests/fixtures/.env", "DEBUG=true\n"),
    ("README.md", "DEBUG=true\n"),
    ("src/app.py", "DEBUG = True\n"),
])
def test_clean_or_non_production_files_pass(tmp_path, path, body):
    root = _repo(tmp_path, {path: body})
    assert guard.main(["x", str(root)]) == 0


def test_report_names_file_and_line_but_exits_nonzero_only_once(tmp_path, capsys):
    root = _repo(tmp_path, {".env": "A=1\nDEBUG=true\n"})
    assert guard.main(["x", str(root)]) == 1
    out = capsys.readouterr().out
    assert ".env:2" in out


def test_empty_repo_is_clean(tmp_path):
    root = _repo(tmp_path, {})
    assert guard.main(["x", str(root)]) == 0


def test_the_real_repository_has_no_debug_in_production_config():
    root = Path(__file__).resolve().parents[2]
    assert guard.scan(root) == []
