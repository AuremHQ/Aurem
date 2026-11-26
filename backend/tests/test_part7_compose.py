"""
test_part7_compose.py — Part VII audit fix 5(a).

The compose build contexts pointed at infra/api and infra/ui, which
never existed, so `compose up --build` failed at the first service.
These tests pin the fixed contexts + the bind-mount, and that both
Dockerfiles are context-relative and serve the ports compose maps.

Run from backend/ with the agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part7_compose.py -q

No Docker daemon is needed (and none is assumed): this validates the
file contents, not a live build.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "infra" / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def test_api_build_context_is_backend(compose):
    api = compose["services"]["api"]
    assert api["build"]["context"] == "../backend"
    assert api["build"]["dockerfile"] == "Dockerfile"


def test_ui_build_context_is_frontend(compose):
    ui = compose["services"]["ui"]
    assert ui["build"]["context"] == "../frontend"
    assert ui["build"]["dockerfile"] == "Dockerfile"


def test_no_missing_infra_subdir_references(compose):
    """No build context or volume source may point at the nonexistent
    infra/api or infra/ui dirs. (Checked structurally — the file's
    own explanatory comments mention ./api in prose.)"""
    for name, svc in compose["services"].items():
        ctx = (svc.get("build") or {}).get("context", "")
        assert ctx not in ("./api", "./ui"), name
        for vol in svc.get("volumes", []):
            src = str(vol).split(":")[0]
            assert src not in ("./api", "./ui"), (name, vol)


def test_api_volume_mounts_backend(compose):
    volumes = compose["services"]["api"]["volumes"]
    assert "../backend:/app:ro" in volumes


def test_outbox_untouched(compose):
    outbox = compose["services"]["outbox"]
    assert outbox["build"]["context"] == "./outbox"
    assert (_REPO_ROOT / "infra" / "outbox" / "Dockerfile").is_file()


def test_dockerfiles_exist_and_match_compose_ports(compose):
    backend_df = (_REPO_ROOT / "backend" / "Dockerfile").read_text()
    assert "COPY . ." in backend_df          # context-relative
    assert "EXPOSE 8002" in backend_df
    assert "8002" in backend_df and "uvicorn" in backend_df
    assert "8002:8002" in compose["services"]["api"]["ports"]

    frontend_df = (_REPO_ROOT / "frontend" / "Dockerfile").read_text()
    assert "COPY . ." in frontend_df          # context-relative
    assert "EXPOSE 3001" in frontend_df
    assert "3001:3001" in compose["services"]["ui"]["ports"]
