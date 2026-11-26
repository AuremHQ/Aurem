"""tests/test_item12_deploy_rbac_x4.py — Item 12 (X4) RBAC fixes.

Proves the Item 12 fixes in `routers/deploy.py`: the three endpoints
that still had auth-only (no RBAC) now enforce the same deploy gates
as the Part XI (1b) fixes —

  * `run_deploy` (POST /run) — deploy-WRITE gate, same as
    `save_config` / `delete_config`. A viewer (deploy: read) and a
    member with no deploy access get 403; owners / writers start
    the run. The check fires before any config lookup, trust-event
    log, or run insert.
  * `get_log` (GET /log/{run_id}) — deploy-READ gate, same as
    `get_config_for_project`. A member with no deploy access gets
    403; readers / owners get the lines. `runs_logs` delegates to
    `get_log`, so it inherits the gate (proven here too).
  * `history` (GET /history) — deploy-READ gate. A member with no
    deploy access gets 403; readers / owners get the run list.

Deliberately NOT covered here (out of scope for X4 — same gap class,
separate decision): `list_runs`, `verify_summary`, `get_run_receipt`
and `post_deploy_event` still have auth-only. See REPORT.md.

Harness mirrors tests/test_part11_deploy_fixes.py exactly (fake
Mongo, stub packages for the import, REAL deploy.py + REAL
team_permissions): run from backend/:
    python -m pytest tests/test_item12_deploy_rbac_x4.py -v
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]


# ─── Fake Mongo ────────────────────────────────────────────────────

class _FakeCursor:
    """Chainable .sort().limit() cursor with async iteration."""

    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, field, direction=1):
        self._rows.sort(key=lambda r: r.get(field) or "",
                        reverse=(direction < 0))
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        async def _gen():
            for row in self._rows:
                yield dict(row)
        return _gen()


class _FakeColl:
    """Minimal async Mongo collection fake with call recording."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.find_one_calls: list[dict] = []
        self.find_calls: list[dict] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[dict] = []
        self.insert_calls: list[dict] = []

    @staticmethod
    def _matches(row: dict, query: dict) -> bool:
        for key, val in query.items():
            if key == "$or":
                if not any(_FakeColl._matches(row, sub) for sub in val):
                    return False
            elif isinstance(val, dict) and "$exists" in val:
                if bool(val["$exists"]) == (key not in row):
                    return False
            elif row.get(key) != val:
                return False
        return True

    async def find_one(self, query, projection=None):
        self.find_one_calls.append(dict(query))
        for row in self.rows:
            if self._matches(row, query):
                return dict(row)
        return None

    def find(self, query, projection=None):
        self.find_calls.append(dict(query))
        matched = [dict(r) for r in self.rows
                   if self._matches(r, query)]
        return _FakeCursor(matched)

    async def insert_one(self, doc):
        self.insert_calls.append(dict(doc))
        self.rows.append(dict(doc))
        return {"inserted_id": doc.get("run_id") or doc.get("kind")}

    async def update_one(self, query, update, upsert=False):
        self.update_calls.append((dict(query), update, upsert))
        for row in self.rows:
            if self._matches(row, query):
                row.update((update.get("$set") or {}))
                return
        if upsert:
            doc = {k: v for k, v in query.items()
                   if not isinstance(v, dict)}
            doc.update(update.get("$set") or {})
            self.rows.append(doc)

    async def delete_one(self, query):
        self.delete_calls.append(dict(query))
        self.rows[:] = [r for r in self.rows
                        if not self._matches(r, query)]


class _FakeMemberColl:
    """team_members stand-in: returns one preset row (or None)."""

    def __init__(self, row=None):
        self.row = row
        self.find_one_calls: list[dict] = []

    async def find_one(self, query, projection=None):
        self.find_one_calls.append(dict(query))
        return dict(self.row) if self.row is not None else None


class _FakeDB:
    def __init__(self, cfg_rows=None, run_rows=None, member_row=None):
        self.aurem_cto_deploy_configs = _FakeColl(cfg_rows)
        self.aurem_cto_deploy_runs = _FakeColl(run_rows)
        self.team_members = _FakeMemberColl(member_row)

    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll


# ─── Module loading (real deploy.py + real team_permissions) ───────

def _mod(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _pkg(name: str, path: Path) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__path__ = [str(path)]  # type: ignore[attr-defined]
    return m


def _load_modules():
    """Import the REAL deploy router from this backend/ tree, with
    stub packages for the siblings that are not seeded here.
    `services` resolves to the real tree dir, so the RBAC checks
    call the REAL team_permissions code against the fake db."""
    if "routers.deploy" in sys.modules:
        return sys.modules["routers.deploy"]

    async def _noop(*args, **kwargs):
        return None

    def _require_db_sync():
        # Production require_db() is SYNC (deploy.py calls it without
        # await); keep the stub sync too, or patch.object() builds an
        # AsyncMock for it and every handler gets a coroutine as `db`.
        raise RuntimeError("require_db stub — tests must patch it")

    sys.modules.setdefault(
        "cto_services", _pkg("cto_services", BACKEND / "cto_services"))
    sys.modules.setdefault(
        "cto_services.auth", _mod("cto_services.auth",
                                   current_dev=AsyncMock()))
    sys.modules.setdefault(
        "cto_services.db", _mod("cto_services.db",
                                 get_db=lambda: None,
                                 require_db=_require_db_sync))
    sys.modules.setdefault(
        "services", _pkg("services", BACKEND / "services"))
    sys.modules.setdefault(
        "services.http", _mod("services.http"))
    sys.modules.setdefault(
        "services.http.client",
        _mod("services.http.client", ext_client=_noop))
    sys.modules.setdefault(
        "routers", _pkg("routers", BACKEND / "routers"))
    return importlib.import_module("routers.deploy")


deploy_mod = _load_modules()


# ─── Helpers ───────────────────────────────────────────────────────

async def _mock_current_dev(_authz):
    return {"user_id": "u_test", "email": "t@test.com"}


def _member_row(user_id, deploy_level):
    """An active team-member row whose deploy permission is exactly
    `deploy_level` (other resources default to read)."""
    perms = {r: "read" for r in (
        "dashboard", "projects", "repo_connect", "loop", "deploy",
        "domain", "integrations", "automations", "reports", "billing",
        "settings", "support", "team_management")}
    perms["deploy"] = deploy_level
    return {
        "user_id": user_id, "status": "active", "owner_id": "u_owner",
        "role_label": "viewer", "permissions": perms,
        "member_id": "m1",
    }


def _patch_auth(db):
    """Context managers patching the router's auth/db lookups."""
    return (patch.object(deploy_mod, "current_dev", _mock_current_dev),
            patch.object(deploy_mod, "require_db", new=lambda: db))


CFG_ROW = {
    "user_id": "u_test", "project_id": None,
    "host": "vps1.example.com", "port": 22, "username": "deploy",
    "repo_path": "/srv/app", "branch": "main",
    "compose_file": "docker-compose.yml",
}


def _run_row(run_id, started_at, **overrides):
    row = {
        "run_id": run_id, "user_id": "u_test", "mode": "deploy",
        "target": "ssh", "host": "vps1.example.com",
        "status": "done", "exit_code": 0,
        "output": ["line1", "line2"],
        "started_at": started_at, "last_update": started_at,
        "finished_at": started_at,
    }
    row.update(overrides)
    return row


# ─── run_deploy — deploy-WRITE gate ────────────────────────────────

@pytest.mark.asyncio
async def test_run_deploy_rejects_viewer_without_deploy_write():
    db = _FakeDB(cfg_rows=[dict(CFG_ROW)],
                 member_row=_member_row("u_viewer", "read"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.run_deploy(deploy_mod.DeployRunBody(),
                                        "Bearer x")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "permission_denied"
    assert exc.value.detail["resource"] == "deploy"
    assert exc.value.detail["required"] == "write"
    # The write check fires before any config lookup or run insert.
    assert db.aurem_cto_deploy_configs.find_one_calls == []
    assert db.aurem_cto_deploy_runs.rows == []


@pytest.mark.asyncio
async def test_run_deploy_rejects_member_with_no_deploy_access():
    db = _FakeDB(cfg_rows=[dict(CFG_ROW)],
                 member_row=_member_row("u_viewer", "none"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.run_deploy(deploy_mod.DeployRunBody(),
                                        "Bearer x")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "permission_denied"
    assert db.aurem_cto_deploy_runs.rows == []


@pytest.mark.asyncio
async def test_run_deploy_owner_passes_and_starts_run():
    db = _FakeDB(cfg_rows=[dict(CFG_ROW)], member_row=None)
    auth, reqdb = _patch_auth(db)
    calls: list = []

    async def _stub_remote(*args, **kwargs):
        calls.append((args, kwargs))

    with auth, reqdb, \
         patch.object(deploy_mod, "_run_deploy_remote",
                      new=_stub_remote):
        out = await deploy_mod.run_deploy(deploy_mod.DeployRunBody(),
                                          "Bearer x")
        await asyncio.sleep(0)  # let the create_task'd stub run
    assert out["mode"] == "deploy"
    assert out["target"] == "ssh"
    assert out["status"] == "running"
    assert len(out["run_id"]) == 16
    assert len(db.aurem_cto_deploy_runs.rows) == 1
    assert db.aurem_cto_deploy_runs.rows[0]["user_id"] == "u_test"
    assert db.aurem_cto_deploy_runs.rows[0]["status"] == "running"
    assert len(calls) == 1
    assert calls[0][0][1] == out["run_id"]  # stub got the same run_id


@pytest.mark.asyncio
async def test_run_deploy_member_with_deploy_write_passes():
    db = _FakeDB(cfg_rows=[dict(CFG_ROW)],
                 member_row=_member_row("u_ed", "write"))
    auth, reqdb = _patch_auth(db)

    async def _stub_remote(*args, **kwargs):
        return None

    with auth, reqdb, \
         patch.object(deploy_mod, "_run_deploy_remote",
                      new=_stub_remote):
        out = await deploy_mod.run_deploy(deploy_mod.DeployRunBody(),
                                          "Bearer x")
        await asyncio.sleep(0)
    assert out["status"] == "running"
    assert len(db.aurem_cto_deploy_runs.rows) == 1


# ─── get_log — deploy-READ gate ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_log_rejects_member_without_read():
    db = _FakeDB(run_rows=[_run_row("r1", "2026-11-26T00:00:00+00:00")],
                 member_row=_member_row("u_viewer", "none"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.get_log("r1", 0, "Bearer x")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "permission_denied"
    assert exc.value.detail["resource"] == "deploy"
    assert exc.value.detail["required"] == "read"
    # The read check fires before any run lookup.
    assert db.aurem_cto_deploy_runs.find_one_calls == []


@pytest.mark.asyncio
async def test_get_log_allows_reader():
    db = _FakeDB(run_rows=[_run_row("r1", "2026-11-26T00:00:00+00:00")],
                 member_row=_member_row("u_viewer", "read"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        out = await deploy_mod.get_log("r1", 0, "Bearer x")
    assert out["run_id"] == "r1"
    assert out["status"] == "done"
    assert out["lines"] == ["line1", "line2"]
    assert out["next_cursor"] == 2


@pytest.mark.asyncio
async def test_get_log_owner_passes():
    db = _FakeDB(run_rows=[_run_row("r1", "2026-11-26T00:00:00+00:00")],
                 member_row=None)
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        out = await deploy_mod.get_log("r1", 1, "Bearer x")
    assert out["lines"] == ["line2"]
    assert out["since"] == 1


@pytest.mark.asyncio
async def test_runs_logs_inherits_read_gate():
    # runs_logs delegates to get_log — no separate gate to add, but
    # prove the delegation actually carries the 403 through.
    db = _FakeDB(run_rows=[_run_row("r1", "2026-11-26T00:00:00+00:00")],
                 member_row=_member_row("u_viewer", "none"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.runs_logs("r1", 0, "Bearer x")
    assert exc.value.status_code == 403
    db2 = _FakeDB(run_rows=[_run_row("r1", "2026-11-26T00:00:00+00:00")],
                  member_row=None)
    auth2, reqdb2 = _patch_auth(db2)
    with auth2, reqdb2:
        out = await deploy_mod.runs_logs("r1", 0, "Bearer x")
    assert out["lines"] == ["line1", "line2"]


# ─── history — deploy-READ gate ────────────────────────────────────

@pytest.mark.asyncio
async def test_history_rejects_member_without_read():
    db = _FakeDB(
        run_rows=[_run_row("r1", "2026-11-26T00:00:00+00:00"),
                  _run_row("r2", "2026-11-26T01:00:00+00:00")],
        member_row=_member_row("u_viewer", "none"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.history("Bearer x")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "permission_denied"
    assert exc.value.detail["required"] == "read"
    # The read check fires before any history query.
    assert db.aurem_cto_deploy_runs.find_calls == []


@pytest.mark.asyncio
async def test_history_allows_reader():
    db = _FakeDB(
        run_rows=[_run_row("r1", "2026-11-26T00:00:00+00:00"),
                  _run_row("r2", "2026-11-26T01:00:00+00:00")],
        member_row=_member_row("u_viewer", "read"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        out = await deploy_mod.history("Bearer x")
    assert [r["run_id"] for r in out["runs"]] == ["r2", "r1"]
    assert len(out["runs"]) == 2


@pytest.mark.asyncio
async def test_history_owner_passes():
    db = _FakeDB(
        run_rows=[_run_row("r1", "2026-11-26T00:00:00+00:00")],
        member_row=None)
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        out = await deploy_mod.history("Bearer x")
    assert [r["run_id"] for r in out["runs"]] == ["r1"]
