"""Checkup router: flag-gated (default OFF), streams progress, persists,
never blocks chat on failure.  Real FastAPI app + TestClient; only the
external edges (auth, DB, GitHub read) are replaced."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import checkup as ck
from tests.test_fix_items_layer import _Coll


class _DB:
    def __init__(self):
        for n in ("cto_open_findings", "project_checkups", "checkup_events", "permission_answers",
                  "first_scan_results", "cto_projects"):
            setattr(self, n, _Coll())
        self.cto_projects.rows.append({"project_id": "p", "user_id": "acct", "github_owner": "o",
                                       "github_repo": "r"})


@pytest.fixture
def env(monkeypatch):
    db = _DB()
    state = {"enabled": True, "off": set(), "tc": {
        "package.json": json.dumps({"dependencies": {"react": "18"}}),
        "src/App.jsx": "import X from './Nope';\nexport default 1;\n"}}

    async def cur(_a):
        return {"user_id": "u1", "tier": "pro"}

    async def perm(user, *_a, **_k):
        return {"effective_account_id": "acct"}

    async def enabled(flag, **_k):
        return state["enabled"] and flag not in state["off"]

    async def tok(_p):
        return "tok"

    async def cache(*_a):
        if isinstance(state["tc"], Exception):
            raise state["tc"]
        return state["tc"]
    monkeypatch.setattr(ck, "current_dev", cur)
    monkeypatch.setattr(ck, "check_permission", perm)
    monkeypatch.setattr(ck, "is_enabled", enabled)
    monkeypatch.setattr(ck, "get_db", lambda: db)
    monkeypatch.setattr(ck, "_build_text_cache", cache)

    async def no_meta(*_a):
        return ["src/App.jsx"], {}
    monkeypatch.setattr("services.checkup_meta.fetch_repo_meta", no_meta)
    monkeypatch.setattr("services.pat_vault.get_repo_token", tok)
    ck._last_run.clear()
    app = FastAPI()
    app.include_router(ck.router, prefix="/api/aurem-dev")
    return TestClient(app), db, state


def _events(resp):
    return [json.loads(ln[6:]) for ln in resp.text.splitlines() if ln.startswith("data: ")]


def test_flag_off_everything_404s(env):
    c, _, st = env
    st["enabled"] = False
    assert c.post("/api/aurem-dev/checkup/run", json={"project_id": "p"}).status_code == 404
    assert c.get("/api/aurem-dev/checkup/latest?project_id=p").status_code == 404


def test_run_streams_persists_and_latest_reads_back(env):
    c, db, _ = env
    r = c.post("/api/aurem-dev/checkup/run", json={"project_id": "p"})
    assert r.status_code == 200
    evs = _events(r)
    assert evs[0]["type"] == "reading_code" and evs[-1]["type"] == "done"
    assert any(i["title_plain"] for i in evs[-1]["items"])
    assert db.project_checkups.rows and db.cto_open_findings.rows
    ev_names = [e["event"] for e in db.checkup_events.rows]
    assert "checkup_started" in ev_names and "checkup_verdict_shown" in ev_names
    lat = c.get("/api/aurem-dev/checkup/latest?project_id=p").json()
    assert lat["items"] and lat["score"] <= 100


def test_cooldown_blocks_hammering(env):
    c, _, _ = env
    assert c.post("/api/aurem-dev/checkup/run", json={"project_id": "p"}).status_code == 200
    assert c.post("/api/aurem-dev/checkup/run", json={"project_id": "p"}).status_code == 429


def test_read_failure_is_a_friendly_event_not_a_crash(env):
    c, _, st = env
    st["tc"] = RuntimeError("github down")
    evs = _events(c.post("/api/aurem-dev/checkup/run", json={"project_id": "p"}))
    assert evs[-1]["type"] == "error" and "chat still works" in evs[-1]["message"]


def test_unknown_project_404_and_missing_project_id_400(env):
    c, _, _ = env
    assert c.post("/api/aurem-dev/checkup/run", json={"project_id": "zzz"}).status_code == 404
    assert c.post("/api/aurem-dev/checkup/run", json={}).status_code == 400


def test_answer_validated_and_ignore_requires_valid_transition(env):
    c, db, _ = env
    assert c.post("/api/aurem-dev/checkup/answer", json={"project_id": "p", "table": "t", "answer": "maybe"}).status_code == 400
    assert c.post("/api/aurem-dev/checkup/answer", json={"project_id": "p", "table": "T", "answer": "public"}).json()["ok"]
    assert db.permission_answers.rows[0]["table"] == "t"
    c.post("/api/aurem-dev/checkup/run", json={"project_id": "p"})
    item_id = db.cto_open_findings.rows[0]["finding_id"]
    assert c.post("/api/aurem-dev/checkup/items/ignore", json={"project_id": "p", "item_id": item_id}).json()["ok"]
    assert not c.post("/api/aurem-dev/checkup/items/ignore", json={"project_id": "p", "item_id": "nope"}).json()["ok"]


def test_fix_and_undo_404_when_fix_loop_flag_off_but_checkup_on(env):
    c, _, st = env
    st["off"] = {"fix_loop_v2"}
    assert c.post("/api/aurem-dev/checkup/items/fix", json={"project_id": "p", "item_id": "x"}).status_code == 404
    assert c.post("/api/aurem-dev/checkup/items/undo", json={"project_id": "p", "item_id": "x"}).status_code == 404


def test_guardian_closes_vanished_items_only_when_flag_on(env):
    c, db, st = env
    st["off"] = {"guardian_rescan"}
    c.post("/api/aurem-dev/checkup/run", json={"project_id": "p"})
    n = len(db.cto_open_findings.rows)
    assert n and all(r["status"] == "open" for r in db.cto_open_findings.rows)
    st["tc"] = {"package.json": json.dumps({"dependencies": {"react": "18"}}), "src/App.jsx": "export default 1;\n"}
    ck._last_run.clear()
    c.post("/api/aurem-dev/checkup/run", json={"project_id": "p"})
    assert all(r["status"] == "open" for r in db.cto_open_findings.rows)       # flag off: untouched
    st["off"] = set()
    ck._last_run.clear()
    c.post("/api/aurem-dev/checkup/run", json={"project_id": "p"})
    assert all(r["status"] == "fixed" and r["fixed_by"] == "proof_rerun" for r in db.cto_open_findings.rows)
