"""Check Library v2 / Group 6 (L13.2, L14.1): fires on bad fixtures, silent on clean."""
from __future__ import annotations

import json

import pytest

from services.checkup_checks_reliability import check_health_endpoint, check_swallowed_errors


# ── L13.2 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path,code", [
    ("src/api.js", "async function load(){\n  try { await fetch('/x'); } catch (e) {}\n}\n"),
    ("src/api.ts", "fetch('/x').catch(() => {});\n"),
    ("src/a.js", "try { run(); } catch {\n}\n"),
    ("app/svc.py", "try:\n    sync()\nexcept Exception:\n    pass\n"),
    ("app/svc.py", "try:\n    sync()\nexcept:\n    pass\n"),
    ("app/svc.py", "try:\n    sync()\nexcept Exception as e: pass\n"),
])
def test_swallowed_error_fires(path, code):
    [item] = check_swallowed_errors({path: code})
    assert item["rule_id"] == "errors_swallowed" and item["severity"] == "medium"
    assert item["evidence"]["file"] == path and item["evidence"]["line"] >= 1


def test_one_item_per_file_with_count():
    code = "try { a(); } catch (e) {}\ntry { b(); } catch (e) {}\n"
    [item] = check_swallowed_errors({"src/a.js": code})
    assert "2 place" in item["evidence"]["detail"]


@pytest.mark.parametrize("path,code", [
    ("src/a.js", "try { run(); } catch (e) { console.error(e); }\n"),
    ("src/a.js", "try { run(); } catch (e) { /* ignore: optional cache */ }\n"),
    ("src/a.js", "fetch('/x').catch((e) => report(e));\n"),
    ("app/s.py", "try:\n    import ujson\nexcept ImportError:\n    pass\n"),
    ("app/s.py", "try:\n    sync()\nexcept Exception:\n    log.exception('sync failed')\n"),
    ("app/s.py", "try:\n    sync()\nexcept Exception:\n    # best effort, cache only\n    pass\n"),
    ("tests/a.test.js", "try { run(); } catch (e) {}\n"),
    ("node_modules/x/a.js", "try { run(); } catch (e) {}\n"),
    ("README.md", "catch (e) {}"),
])
def test_swallowed_error_silent_on_clean(path, code):
    assert check_swallowed_errors({path: code}) == []


# ── L14.1 ─────────────────────────────────────────────────────────────
EXPRESS = {"package.json": json.dumps({"dependencies": {"express": "4"}}),
           "server/index.js": "const express = require('express');\nconst app = express();\napp.get('/api/items', h);\n"}
FASTAPI = {"requirements.txt": "fastapi\nuvicorn\n", "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/items')\ndef i(): ...\n"}


def test_server_without_health_fires_with_entry_evidence():
    [express] = check_health_endpoint(EXPRESS)
    assert express["rule_id"] == "no_health_endpoint" and express["severity"] == "medium"
    assert (express["evidence"]["file"], express["evidence"]["line"]) == ("server/index.js", 2)
    [fast] = check_health_endpoint(FASTAPI)
    assert (fast["evidence"]["file"], fast["evidence"]["line"]) == ("app/main.py", 2)


@pytest.mark.parametrize("extra", [
    {"server/health.js": "router.get('/health', (req, res) => res.send('ok'));"},
    {"server/h.js": "app.get('/api/healthz', h);"},
    {"app/h.py": "@app.get('/ping')\ndef ping(): ..."},
    {"Dockerfile": "FROM node\nHEALTHCHECK CMD curl -f http://localhost/ || exit 1\n"},
])
def test_server_with_health_silent(extra):
    assert check_health_endpoint({**EXPRESS, **FASTAPI, **extra}) == []


def test_frontend_only_or_serverless_projects_silent():
    spa = {"package.json": json.dumps({"dependencies": {"react": "18", "vite": "6"}}), "src/App.jsx": "export default () => null"}
    nxt = {"package.json": json.dumps({"dependencies": {"next": "14", "react": "18"}}), "app/page.tsx": "export default () => null"}
    assert check_health_endpoint(spa) == [] and check_health_endpoint(nxt) == []


def test_server_dep_without_provable_entry_is_silent():
    assert check_health_endpoint({"package.json": json.dumps({"dependencies": {"express": "4"}})}) == []
