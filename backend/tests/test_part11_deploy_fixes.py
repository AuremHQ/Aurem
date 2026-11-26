"""tests/test_part11_deploy_fixes.py — Part XI (ORA audit) fixes.

Proves the Part XI fixes in `routers/deploy.py`:

  1(a) Plaintext secrets / broken vault path — `save_config` now
       FAILS CLOSED: with no vault configured it refuses the write
       (503 `vault_unavailable`, nothing reaches Mongo) instead of
       500ing on the undefined `is_vault_available()` NameError or
       storing the secret in plaintext. The `encrypt` hook is a
       real-vault integration point, not a cipher: by default it
       raises, and a backend that hands plaintext back is refused.
  1(b) RBAC — `delete_config` now enforces the same deploy-write
       gate as `save_config`; `get_config_for_project` now enforces
       the deploy-read gate. A viewer (deploy: read) gets 403 on
       delete; a member with no deploy access gets 403 on the
       project-config read; owners / writers still pass.
  1(c) SSH command injection — `_deploy_command` validates
       repo/branch/compose against a safe allowlist at save time
       (`save_config` → 400) and again at run time (hostile stored
       values → `echo 'bad deploy config' && exit 2`), and passes
       every interpolated value through `shlex.quote()` at every
       site in all four modes plus the final DEPLOY_HEAD echo.
       Benign commands are byte-identical to before the fix.

Deliberately NOT covered here (not built in this lane):
  Item 2 (CI Run #2202) — UNPROVEN at run level, pre-existing, not
  new; no code fix belongs to this patch. `known_hosts=None` in
  `_run_deploy_remote` — noted by the verifier, outside the Part XI
  fix scope; not changed here.

How it runs
-----------
The backend's full dependency tree is not installed in every
sandbox, so the module under test is imported with lightweight
stub packages installed in sys.modules for the import only — the
REAL `deploy.py` (and the REAL `services/team_permissions.py`,
which the RBAC checks call) are always loaded from this backend/
tree. Run from backend/:
    python -m pytest tests/test_part11_deploy_fixes.py -v
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]


# ─── Fake Mongo ────────────────────────────────────────────────────

class _FakeColl:
    """Minimal async Mongo collection fake with call recording."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.find_one_calls: list[dict] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[dict] = []

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
    def __init__(self, cfg_rows=None, member_row=None):
        self.aurem_cto_deploy_configs = _FakeColl(cfg_rows)
        self.aurem_cto_deploy_runs = _FakeColl()
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

PEM_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    + ("A" * 50)
    + "\n-----END OPENSSH PRIVATE KEY-----"
)


def _ssh_body(**overrides):
    kw = dict(
        host="vps1.example.com",
        port=22,
        username="deploy",
        private_key=PEM_KEY,
        repo_path="/srv/app1",
        branch="main",
        compose_file="docker-compose.yml",
    )
    kw.update(overrides)
    return deploy_mod.DeployConfigBody(**kw)


def _ftp_body(**overrides):
    kw = dict(
        host="ftp.example.com",
        port=21,
        username="ftpuser",
        private_key="ftp-password-" + ("x" * 30),
        repo_path="/srv/app1",
        branch="main",
        compose_file="docker-compose.yml",
        target="ftp",
        remote_dir="/public_html",
    )
    kw.update(overrides)
    return deploy_mod.DeployConfigBody(**kw)


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


# ─── 1(a) — vault fail-closed ──────────────────────────────────────


def test_is_vault_available_defined_and_returns_false():
    # Regression for the live NameError: the name must exist and the
    # honest answer today is False (no vault backend in this tree).
    assert deploy_mod.is_vault_available() is False


@pytest.mark.asyncio
async def test_save_config_ssh_refused_when_no_vault():
    db = _FakeDB()
    body = _ssh_body()
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.save_config(body, "Bearer x")
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "vault_unavailable"
    # Nothing was written: no update call, no row in the collection.
    assert db.aurem_cto_deploy_configs.update_calls == []
    assert db.aurem_cto_deploy_configs.rows == []


@pytest.mark.asyncio
async def test_save_config_ftp_password_refused_when_no_vault():
    db = _FakeDB()
    body = _ftp_body()
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.save_config(body, "Bearer x")
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "vault_unavailable"
    assert db.aurem_cto_deploy_configs.update_calls == []
    assert db.aurem_cto_deploy_configs.rows == []


@pytest.mark.asyncio
async def test_save_config_with_vault_stubbed_stores_encrypted_value():
    # With a vault stubbed available (both hooks patched, as the old
    # stale tests already did), the path works — and what lands in
    # Mongo is the ENCRYPTED value, never the submitted secret.
    db = _FakeDB()
    body = _ssh_body()
    auth, reqdb = _patch_auth(db)
    with auth, reqdb, \
         patch.object(deploy_mod, "is_vault_available",
                      return_value=True), \
         patch.object(deploy_mod, "encrypt",
                      new=AsyncMock(return_value="enc:test")):
        out = await deploy_mod.save_config(body, "Bearer x")
    assert out == {"ok": True, "project_id": None, "target": "ssh"}
    assert len(db.aurem_cto_deploy_configs.update_calls) == 1
    _flt, payload, _upsert = db.aurem_cto_deploy_configs.update_calls[0]
    stored = payload["$set"]["private_key_enc"]
    assert stored == "enc:test"
    assert stored != PEM_KEY
    assert PEM_KEY not in str(payload)
    assert payload["$set"]["secret_kind"] == "ssh_private_key"


@pytest.mark.asyncio
async def test_save_config_with_vault_but_encrypt_raising_refuses():
    # Vault claims to be available but encryption itself fails:
    # still refuse, still write nothing — never fall back to
    # plaintext.
    db = _FakeDB()
    body = _ssh_body()
    auth, reqdb = _patch_auth(db)
    with auth, reqdb, \
         patch.object(deploy_mod, "is_vault_available",
                      return_value=True), \
         patch.object(deploy_mod, "encrypt",
                      new=AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.save_config(body, "Bearer x")
    assert exc.value.status_code == 501
    assert exc.value.detail["code"] == "vault_encrypt_unavailable"
    assert db.aurem_cto_deploy_configs.update_calls == []
    assert db.aurem_cto_deploy_configs.rows == []


@pytest.mark.asyncio
async def test_save_config_refuses_plaintext_echo_from_encrypt():
    # A misconfigured "encryption" backend that hands the plaintext
    # back must not silently downgrade to plaintext storage.
    db = _FakeDB()
    body = _ssh_body()
    auth, reqdb = _patch_auth(db)
    with auth, reqdb, \
         patch.object(deploy_mod, "is_vault_available",
                      return_value=True), \
         patch.object(deploy_mod, "encrypt",
                      new=AsyncMock(side_effect=lambda u, v, kind=None: v)):
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.save_config(body, "Bearer x")
    assert exc.value.status_code == 501
    assert exc.value.detail["code"] == "vault_encrypt_unavailable"
    assert db.aurem_cto_deploy_configs.update_calls == []


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_encrypt_hook_raises_by_default():
    # The default hook is not a cipher and not a stub that echoes:
    # it refuses, so no caller can store through it by accident.
    with pytest.raises(NotImplementedError):
        await deploy_mod.encrypt("u_test", "secret", kind="ssh_private_key")


# ─── 1(b) — RBAC on delete_config + get_config_for_project ────────


@pytest.mark.asyncio
async def test_delete_config_rejects_viewer_without_deploy_write():
    db = _FakeDB(member_row=_member_row("u_viewer", "read"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.delete_config("", "Bearer x")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "permission_denied"
    assert exc.value.detail["resource"] == "deploy"
    assert exc.value.detail["required"] == "write"
    assert db.aurem_cto_deploy_configs.delete_calls == []


@pytest.mark.asyncio
async def test_delete_config_rejects_member_with_no_deploy_access():
    db = _FakeDB(member_row=_member_row("u_viewer", "none"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.delete_config("p_app1", "Bearer x")
    assert exc.value.status_code == 403
    assert db.aurem_cto_deploy_configs.delete_calls == []


@pytest.mark.asyncio
async def test_delete_config_owner_passes_and_stays_scoped():
    db = _FakeDB(member_row=None)  # no team row => account owner
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        out = await deploy_mod.delete_config("p_app1", "Bearer x")
    assert out == {"ok": True, "project_id": "p_app1"}
    assert len(db.aurem_cto_deploy_configs.delete_calls) == 1
    assert db.aurem_cto_deploy_configs.delete_calls[0] == {
        "user_id": "u_test", "project_id": "p_app1"}


@pytest.mark.asyncio
async def test_delete_config_member_with_deploy_write_passes():
    db = _FakeDB(member_row=_member_row("u_ed", "write"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        out = await deploy_mod.delete_config("", "Bearer x")
    assert out == {"ok": True, "project_id": None}
    assert len(db.aurem_cto_deploy_configs.delete_calls) == 1


@pytest.mark.asyncio
async def test_get_config_for_project_rejects_member_without_read():
    db = _FakeDB(
        cfg_rows=[{"user_id": "u_test", "project_id": "p_app1",
                   "host": "vps1.example.com"}],
        member_row=_member_row("u_viewer", "none"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.get_config_for_project("p_app1", "Bearer x")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "permission_denied"
    # The read check fires before any config lookup.
    assert db.aurem_cto_deploy_configs.find_one_calls == []


@pytest.mark.asyncio
async def test_get_config_for_project_allows_reader():
    db = _FakeDB(
        cfg_rows=[{"user_id": "u_test", "project_id": "p_app1",
                   "host": "vps1.example.com", "port": 22,
                   "username": "deploy", "repo_path": "/srv/app1",
                   "branch": "main", "compose_file": "docker-compose.yml"}],
        member_row=_member_row("u_viewer", "read"))
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        out = await deploy_mod.get_config_for_project("p_app1", "Bearer x")
    assert out["configured"] is True
    assert out["host"] == "vps1.example.com"
    assert "private_key_enc" not in out


@pytest.mark.asyncio
async def test_get_config_for_project_owner_passes():
    db = _FakeDB(cfg_rows=[], member_row=None)
    auth, reqdb = _patch_auth(db)
    with auth, reqdb:
        out = await deploy_mod.get_config_for_project("p_x", "Bearer x")
    assert out == {"configured": False}


# ─── 1(c) — _deploy_command injection ──────────────────────────────

BENIGN_CFG = {
    "repo_path": "/srv/app",
    "branch": "main",
    "compose_file": "docker-compose.yml",
}

EXPECTED = {
    "deploy": (
        "set -e; (cd /srv/app && git fetch --all --prune && "
        "git checkout main && git pull --ff-only origin main && "
        "docker compose -f docker-compose.yml pull && "
        "docker compose -f docker-compose.yml up -d --remove-orphans "
        "--build) && "
        'echo "DEPLOY_HEAD=$(git -C /srv/app rev-parse HEAD)"'
    ),
    "dry_run": (
        "set -e; (cd /srv/app && git fetch --all --prune && "
        "docker compose -f docker-compose.yml config --quiet && "
        "echo DRY_RUN_OK) && "
        'echo "DEPLOY_HEAD=$(git -C /srv/app rev-parse HEAD)"'
    ),
    "rollback": (
        "set -e; (cd /srv/app && git reset --hard HEAD~1 && "
        "docker compose -f docker-compose.yml up -d --remove-orphans) && "
        'echo "DEPLOY_HEAD=$(git -C /srv/app rev-parse HEAD)"'
    ),
    "revert_to": (
        "set -e; (cd /srv/app && git fetch --all --prune && "
        "git revert --no-edit abc1234 && git push origin main && "
        "docker compose -f docker-compose.yml up -d --build "
        "--remove-orphans) && "
        'echo "DEPLOY_HEAD=$(git -C /srv/app rev-parse HEAD)"'
    ),
}


@pytest.mark.parametrize("mode", ["deploy", "dry_run", "rollback"])
def test_deploy_command_benign_is_byte_identical(mode):
    # shlex.quote() leaves safe values unquoted, so legitimate
    # configs must produce exactly the pre-fix command string.
    cfg = dict(BENIGN_CFG)
    assert deploy_mod._deploy_command(cfg, mode) == EXPECTED[mode]


def test_deploy_command_benign_revert_to_is_byte_identical():
    cfg = dict(BENIGN_CFG, _revert_sha="abc1234")
    assert deploy_mod._deploy_command(cfg, "revert_to") == EXPECTED["revert_to"]


@pytest.mark.parametrize("branch", [
    "main; rm -rf /",
    "main && rm -rf /",
    "$(id)",
    "main`id`",
    "main\nid",
    "main | id",
    "-bad",
    "../evil",
    "main; touch /tmp/pwned",
])
def test_deploy_command_hostile_branch_neutralised(branch):
    cfg = dict(BENIGN_CFG, branch=branch)
    for mode in ("deploy", "dry_run", "rollback", "revert_to"):
        cmd = deploy_mod._deploy_command(
            dict(cfg, _revert_sha="abc1234"), mode)
        assert cmd == "echo 'bad deploy config' && exit 2"
        assert "rm -rf" not in cmd
        assert "touch" not in cmd


@pytest.mark.parametrize("repo", [
    "/srv/x$(id)",
    "/srv/app; id > /tmp/pwned",
    "relative/path",
    "/srv/../etc",
    "/srv/app\nid",
    "/srv/app`id`",
])
def test_deploy_command_hostile_repo_neutralised(repo):
    cfg = dict(BENIGN_CFG, repo_path=repo)
    cmd = deploy_mod._deploy_command(cfg, "deploy")
    assert cmd == "echo 'bad deploy config' && exit 2"
    assert "$(id)" not in cmd
    assert "pwned" not in cmd


@pytest.mark.parametrize("compose", [
    "a.yml; rm -rf /",
    "../x.yml",
    "/abs.yml",
    "a.yml$(id)",
    "a.yml\nid",
])
def test_deploy_command_hostile_compose_neutralised(compose):
    cfg = dict(BENIGN_CFG, compose_file=compose)
    cmd = deploy_mod._deploy_command(cfg, "deploy")
    assert cmd == "echo 'bad deploy config' && exit 2"
    assert "rm -rf" not in cmd


def test_deploy_command_bad_sha_still_rejected():
    cfg = dict(BENIGN_CFG, _revert_sha="not-a-sha!")
    assert deploy_mod._deploy_command(cfg, "revert_to") == \
        "echo 'bad sha' && exit 2"


def test_deploy_command_no_repo_still_rejected():
    cfg = dict(BENIGN_CFG, repo_path="")
    assert deploy_mod._deploy_command(cfg, "deploy") == \
        "echo 'no repo_path configured' && exit 2"


def test_deploy_command_quoting_neutralises_injection_in_real_shell(tmp_path):
    """Isolate the quoting layer: bypass ONLY the allowlist and run
    the built command in a real bash with stub git/docker. If the
    branch payload escaped quoting, `touch <marker>` would run and
    the marker file would exist afterwards. It must not."""
    marker = tmp_path / "PWNED"
    repo = tmp_path / "app"
    repo.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls_log = tmp_path / "calls.log"
    calls_log.write_text("")
    for name in ("git", "docker"):
        script = bindir / name
        script.write_text(
            "#!/bin/sh\n"
            f'echo "{name} $@" >> "{calls_log}"\n')
        script.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin")
    with patch.object(deploy_mod, "_is_safe_branch",
                      lambda v: True), \
         patch.object(deploy_mod, "_is_safe_repo_path",
                      lambda v: True), \
         patch.object(deploy_mod, "_is_safe_compose_file",
                      lambda v: True):
        cmd = deploy_mod._deploy_command(
            {"repo_path": str(repo),
             "branch": f"main; touch {marker}",
             "compose_file": "docker-compose.yml"},
            "deploy")
    # The payload survives only as ONE single-quoted shell word.
    assert f"'main; touch {marker}'" in cmd
    proc = subprocess.run(["bash", "-c", cmd], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    assert not marker.exists()
    log = calls_log.read_text()
    assert "git checkout main; touch " in log  # one arg, not a command


# ─── 1(c) — save-time allowlist ────────────────────────────────────


@pytest.mark.parametrize("field,value,detail", [
    ("branch", "main; rm -rf /", "invalid_branch"),
    ("branch", "$(id)", "invalid_branch"),
    ("branch", "main\nid", "invalid_branch"),
    ("repo_path", "/srv/x$(id)", "invalid_repo_path"),
    ("repo_path", "/srv/app; id", "invalid_repo_path"),
    ("compose_file", "a.yml; rm -rf /", "invalid_compose_file"),
    ("compose_file", "../x.yml", "invalid_compose_file"),
])
@pytest.mark.asyncio
async def test_save_config_rejects_hostile_values(field, value, detail):
    # Hostile values are a client error (400) — and nothing is
    # written. The vault gate runs first (as in the original code,
    # where the 503 precedes even the PEM check), so the vault is
    # stubbed available here to reach the allowlist check.
    db = _FakeDB()
    body = _ssh_body(**{field: value})
    auth, reqdb = _patch_auth(db)
    with auth, reqdb, \
         patch.object(deploy_mod, "is_vault_available",
                      return_value=True):
        with pytest.raises(HTTPException) as exc:
            await deploy_mod.save_config(body, "Bearer x")
    assert exc.value.status_code == 400
    assert exc.value.detail == detail
    assert db.aurem_cto_deploy_configs.update_calls == []
