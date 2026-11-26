"""tests/test_item7_known_hosts.py — audit X3, decision 7/13 (2026-11-26).

Host-key verification is ON in both SSH deploy paths:

  * `routers/deploy.py::_run_deploy_remote` (asyncssh) — the old
    `known_hosts=None` disabled verification entirely (any server key
    accepted). The parameter is now gone, so asyncssh falls back to
    its default, ~/.ssh/known_hosts on the backend host: an unknown
    or mismatched host key raises HostKeyNotVerifiable and the run
    fails closed.
  * `services/ftp_ssh_deploy.py::deploy_via_ssh` (paramiko) — the old
    `AutoAddPolicy()` silently trusted any server key. It is now
    `load_system_host_keys()` + `RejectPolicy()`: an unknown host
    raises SSHException and the deploy returns ok=False.

How it runs
-----------
Same stub pattern as test_part11_deploy_fixes.py: the REAL deploy.py
(and the REAL ftp_ssh_deploy.py) are loaded from this backend/ tree
with lightweight stub packages in sys.modules for the import only.
Run from backend/:
    python -m pytest tests/test_item7_known_hosts.py -v

Live tests (real asyncssh server on 127.0.0.1) are skipped when
asyncssh is not installed — asyncssh is NOT a declared dependency
(it is missing from backend/requirements.txt), so CI may not have
it. The mock tests below need no real SSH library at all.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]


# ─── Fake Mongo (same shape as test_part11_deploy_fixes.py) ─────────


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.update_calls: list[tuple] = []

    async def update_one(self, query, update, **kwargs):
        self.update_calls.append((dict(query), update))
        # Apply the $set / $push the deploy worker relies on.
        for row in self.rows:
            if row.get("run_id") == query.get("run_id"):
                if "$set" in update:
                    row.update(update["$set"])
                if "$push" in update:
                    for k, v in update["$push"].items():
                        row.setdefault(k, []).append(v)
                return
        new = {"run_id": query.get("run_id")}
        if "$set" in update:
            new.update(update["$set"])
        if "$push" in update:
            for k, v in update["$push"].items():
                new.setdefault(k, []).append(v)
        self.rows.append(new)


class _FakeDB:
    def __init__(self):
        self.aurem_cto_deploy_runs = _FakeColl()

    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll


def _mod(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _pkg(name: str, path: Path) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__path__ = [str(path)]  # type: ignore[attr-defined]
    return m


def _load_deploy():
    """Import the REAL deploy router (same stub set as test_part11)."""
    if "routers.deploy" in sys.modules:
        return sys.modules["routers.deploy"]

    async def _noop(*args, **kwargs):
        return None

    def _require_db_sync():
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
    sys.modules.setdefault("services.http", _mod("services.http"))
    # _run_deploy_remote logs via services.trust_surface_events
    # after a run finishes (rc 0 or not) — stub it like test_part11
    # stubs the other siblings, or every ok run 500s on the import.
    sys.modules.setdefault(
        "services.trust_surface_events",
        _mod("services.trust_surface_events",
             log_trust_event=AsyncMock()))
    sys.modules.setdefault(
        "services.http.client",
        _mod("services.http.client", ext_client=_noop))
    sys.modules.setdefault(
        "routers", _pkg("routers", BACKEND / "routers"))
    return importlib.import_module("routers.deploy")


def _load_ftp_ssh_deploy():
    """Import the REAL services/ftp_ssh_deploy.py (stdlib-only imports)."""
    if "services.ftp_ssh_deploy" in sys.modules:
        return sys.modules["services.ftp_ssh_deploy"]
    sys.modules.setdefault(
        "services", _pkg("services", BACKEND / "services"))
    return importlib.import_module("services.ftp_ssh_deploy")


deploy_mod = _load_deploy()
ftp_mod = _load_ftp_ssh_deploy()


# ─── Static guards: the bypass must not come back ──────────────────


def test_deploy_source_has_no_known_hosts_none():
    src = (BACKEND / "routers" / "deploy.py").read_text()
    code = [l for l in src.splitlines() if not l.strip().startswith("#")]
    assert not any("known_hosts=None" in l for l in code)
    assert "known_hosts" in src  # the explanatory comment stays


def test_ftp_ssh_source_has_no_auto_add_policy():
    src = (BACKEND / "services" / "ftp_ssh_deploy.py").read_text()
    code = [l for l in src.splitlines() if not l.strip().startswith("#")]
    assert not any("AutoAddPolicy" in l for l in code)
    assert "RejectPolicy" in src
    assert "load_system_host_keys" in src


# ─── Fake asyncssh for _run_deploy_remote ──────────────────────────


class _FakeStream:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, rc=0):
        self.stdout = _FakeStream([])
        self.stderr = _FakeStream([])
        self._rc = rc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def wait(self):
        return self._rc


class _FakeConn:
    def __init__(self, rc=0):
        self._rc = rc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def create_process(self, command):
        return _FakeProc(self._rc)


def _fake_asyncssh(monkeypatch, connect_fn):
    fake = _mod("asyncssh",
                import_private_key=lambda pk: "FAKE-KEY",
                connect=connect_fn)
    monkeypatch.setitem(sys.modules, "asyncssh", fake)
    return fake


def _cfg(**overrides):
    cfg = {"host": "vps1.example.com", "port": 22, "username": "deploy",
           "private_key_enc": "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n"
                              "-----END OPENSSH PRIVATE KEY-----"}
    cfg.update(overrides)
    return cfg


@pytest.mark.asyncio
async def test_run_deploy_remote_does_not_disable_host_key_verification(monkeypatch):
    """The fix: `asyncssh.connect` is called WITHOUT `known_hosts`
    (the old call passed known_hosts=None). Absent the parameter,
    asyncssh verifies against ~/.ssh/known_hosts — see the live
    tests below for what that default does.

    NOTE: the fake `connect` is a plain function returning the fake
    conn (not `async def`): real asyncssh.connect returns an object
    that is both awaitable and an async context manager, and
    `async with` on a bare coroutine is never awaited."""
    seen = {}

    def _connect(*args, **kwargs):
        seen.update(kwargs)
        seen["_args"] = args
        return _FakeConn(rc=0)

    _fake_asyncssh(monkeypatch, _connect)
    db = _FakeDB()
    with patch.object(deploy_mod, "require_db", new=lambda: db):
        await deploy_mod._run_deploy_remote(
            "u1", "run1", _cfg(), "echo hi", mode="dry_run")
    assert "known_hosts" not in seen, \
        f"host-key verification disabled again: {seen}"
    assert seen["_args"][0] == "vps1.example.com"
    assert seen["port"] == 22
    row = db.aurem_cto_deploy_runs.rows[0]
    assert row["status"] == "ok", row


@pytest.mark.asyncio
async def test_run_deploy_remote_unknown_host_fails_closed(monkeypatch):
    """An untrusted host key must fail the run — never connect."""
    real_asyncssh = pytest.importorskip("asyncssh")

    def _connect(*args, **kwargs):
        raise real_asyncssh.HostKeyNotVerifiable(
            "Host key is not trusted for host vps1.example.com")

    _fake_asyncssh(monkeypatch, _connect)
    db = _FakeDB()
    with patch.object(deploy_mod, "require_db", new=lambda: db):
        await deploy_mod._run_deploy_remote(
            "u1", "run1", _cfg(), "echo hi", mode="dry_run")
    row = db.aurem_cto_deploy_runs.rows[0]
    assert row["status"] == "failed", row
    assert "HostKeyNotVerifiable" in " ".join(row.get("output", [])), row


# ─── deploy_via_ssh with a fake paramiko SSHClient ─────────────────


class _FakeSFTPFile:
    def __init__(self, writes, path):
        self._writes = writes
        self._path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, data):
        self._writes[self._path] = data


class _FakeSFTP:
    def __init__(self):
        self.writes: dict = {}

    def mkdir(self, path):
        pass

    def open(self, path, mode):
        return _FakeSFTPFile(self.writes, path)

    def close(self):
        pass


class _FakeSSHClient:
    instances: list = []

    def __init__(self):
        self.load_system_calls = 0
        self.policy = None
        self.connect_kwargs = None
        self.raise_on_connect = None
        self.sftp = _FakeSFTP()
        _FakeSSHClient.instances.append(self)

    def load_system_host_keys(self):
        self.load_system_calls += 1

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        if self.raise_on_connect is not None:
            raise self.raise_on_connect

    def open_sftp(self):
        return self.sftp

    def close(self):
        pass


@pytest.mark.asyncio
async def test_deploy_via_ssh_uses_reject_policy(monkeypatch):
    """Real paramiko policy classes, fake transport: the client must
    load the system known_hosts and refuse unknown hosts."""
    import paramiko

    _FakeSSHClient.instances.clear()
    monkeypatch.setattr(paramiko, "SSHClient", _FakeSSHClient)
    res = await ftp_mod.deploy_via_ssh(
        host="vps1.example.com", port=22, user="deploy",
        password="pw", remote_dir="/srv/app",
        files={"a.txt": b"hello"})
    client = _FakeSSHClient.instances[0]
    assert client.load_system_calls == 1
    assert isinstance(client.policy, paramiko.RejectPolicy), \
        f"policy is {type(client.policy).__name__}, not RejectPolicy"
    assert not isinstance(client.policy, paramiko.AutoAddPolicy)
    assert client.connect_kwargs["hostname"] == "vps1.example.com"
    assert res["ok"] is True, res
    assert res["uploaded"] == 1
    assert client.sftp.writes["/srv/app/a.txt"] == b"hello"


@pytest.mark.asyncio
async def test_deploy_via_ssh_unknown_host_returns_not_ok(monkeypatch):
    """RejectPolicy refusing an unknown host surfaces as ok=False
    with the refusal in `fatal` — never a silent pass."""
    import paramiko

    _FakeSSHClient.instances.clear()
    monkeypatch.setattr(paramiko, "SSHClient", _FakeSSHClient)
    _orig_connect = _FakeSSHClient.connect

    def _refuse(self, **kwargs):
        raise paramiko.SSHException(
            "Server 'vps1.example.com' not found in known_hosts")

    monkeypatch.setattr(_FakeSSHClient, "connect", _refuse)
    res = await ftp_mod.deploy_via_ssh(
        host="vps1.example.com", port=22, user="deploy",
        password="pw", remote_dir="/srv/app",
        files={"a.txt": b"hello"})
    assert res["ok"] is False, res
    assert "not found in known_hosts" in (res.get("fatal") or ""), res
    assert res["uploaded"] == 0


def test_paramiko_bracketed_port_entry_matches(monkeypatch, tmp_path):
    """Real paramiko, no server: the `[host]:port` known_hosts line
    the iter367 fixture writes (and `ssh-keyscan -H -p` produces)
    must match the key for a non-22 port — this is what the SFTP
    live tests rely on."""
    import paramiko

    key = paramiko.RSAKey.generate(1024)
    other = paramiko.RSAKey.generate(1024)
    pub = f"{key.get_name()} {key.get_base64()}"
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:2222 {pub}\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    os.makedirs(tmp_path / ".ssh", exist_ok=True)
    (tmp_path / ".ssh" / "known_hosts").write_text(
        f"[127.0.0.1]:2222 {pub}\n")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    system_keys = client._system_host_keys
    assert system_keys.check("[127.0.0.1]:2222", key) is True
    assert system_keys.check("[127.0.0.1]:2222", other) is False
    assert system_keys.check("127.0.0.1", key) is False  # port matters


# ─── Live tests: real asyncssh server + real deploy code ───────────
# Skipped when asyncssh is not installed (it is not a declared dep).


def _make_live_server(asyncssh):
    """Server classes for the _run_deploy_remote live tests. Built
    lazily (after importorskip) — asyncssh is not a declared dep,
    so nothing here may import it at module scope.

    The session MUST subclass asyncssh.SSHServerSession — a plain
    class lacks `connection_lost` / `session_started` and the
    channel fails ("Session request failed" on the client)."""

    class _LiveSession(asyncssh.SSHServerSession):
        """Accept any exec, exit 0 immediately.

        connection_made MUST store the channel: the base class's
        connection_made is a no-op (it does not set self._chan).
        The exit MUST happen in session_started, not in
        exec_requested: exiting inside exec_requested closes the
        channel before the exec SUCCESS reply is sent, and the
        client fails with "Session request failed"."""

        def connection_made(self, chan):
            self._chan = chan

        def exec_requested(self, command):
            return True

        def session_started(self):
            self._chan.exit(0)

    class _LiveServer(asyncssh.SSHServer):
        """Publickey auth always OK, sessions exit 0."""

        def public_key_auth_supported(self):
            return True

        def validate_public_key(self, username, key):
            return True

        def session_requested(self):
            return _LiveSession()

    return _LiveServer


async def _run_blocking_deploy_via_ssh(**kwargs):
    """paramiko is blocking: run deploy_via_ssh in a worker thread
    with its own event loop, so the asyncssh server sharing this
    test's loop keeps breathing. Calling it directly starves the
    server (banner timeout / connection reset)."""
    import asyncio as _asyncio

    return await _asyncio.to_thread(
        lambda: _asyncio.run(ftp_mod.deploy_via_ssh(**kwargs)))


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.asyncio
async def test_run_deploy_remote_live_trusted_host(monkeypatch, tmp_path):
    """End-to-end, no mocks on the SSH path: the real deploy worker
    against a real asyncssh server whose key IS in the temp HOME's
    ~/.ssh/known_hosts → run ok."""
    asyncssh = pytest.importorskip("asyncssh")

    port = _free_port()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    pub = host_key.export_public_key().decode().strip()
    client_home = tmp_path / "client_home"
    (client_home / ".ssh").mkdir(parents=True)
    (client_home / ".ssh" / "known_hosts").write_text(
        f"[127.0.0.1]:{port} {pub}\n")
    monkeypatch.setenv("HOME", str(client_home))

    client_key = asyncssh.generate_private_key("ssh-ed25519")
    priv_pem = client_key.export_private_key().decode()

    server = await asyncssh.create_server(
        _make_live_server(asyncssh), "127.0.0.1", port,
        server_host_keys=[host_key])
    try:
        db = _FakeDB()
        cfg = _cfg(host="127.0.0.1", port=port, username="deploy",
                   private_key_enc=priv_pem)
        with patch.object(deploy_mod, "require_db", new=lambda: db):
            await deploy_mod._run_deploy_remote(
                "u1", "run1", cfg, "echo hi", mode="dry_run")
    finally:
        server.close()
        await server.wait_closed()
    row = db.aurem_cto_deploy_runs.rows[0]
    # Host-key verification passed: the connection was established
    # and the remote command ran to exit 0.
    # NOTE (pre-existing, NOT this lane): row["status"] is "failed"
    # here despite exit 0 — on asyncssh 2.x, `proc.wait()` returns
    # an SSHCompletedProcess object, so the worker's `rc == 0` check
    # is False and the run is mis-recorded (see report §5). The
    # assertion below is version-tolerant on purpose: it proves the
    # command completed rc 0 without depending on that bug.
    assert "$ echo hi" in " ".join(row.get("output", [])), row
    assert "HostKeyNotVerifiable" not in " ".join(row.get("output", [])), row
    _exc = row.get("exit_code")
    assert getattr(_exc, "exit_status", _exc) == 0, row


@pytest.mark.asyncio
async def test_run_deploy_remote_live_untrusted_host(monkeypatch, tmp_path):
    """Same live server, empty HOME (no known_hosts file at all):
    the run must fail closed with HostKeyNotVerifiable — this is
    what `known_hosts=None` used to silently allow."""
    asyncssh = pytest.importorskip("asyncssh")

    port = _free_port()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))

    client_key = asyncssh.generate_private_key("ssh-ed25519")
    priv_pem = client_key.export_private_key().decode()

    server = await asyncssh.create_server(
        _make_live_server(asyncssh), "127.0.0.1", port,
        server_host_keys=[host_key])
    try:
        db = _FakeDB()
        cfg = _cfg(host="127.0.0.1", port=port, username="deploy",
                   private_key_enc=priv_pem)
        with patch.object(deploy_mod, "require_db", new=lambda: db):
            await deploy_mod._run_deploy_remote(
                "u1", "run1", cfg, "echo hi", mode="dry_run")
    finally:
        server.close()
        await server.wait_closed()
    row = db.aurem_cto_deploy_runs.rows[0]
    assert row["status"] == "failed", row
    assert "HostKeyNotVerifiable" in " ".join(row.get("output", [])), row


@pytest.mark.asyncio
async def test_deploy_via_ssh_live_trusted_host(monkeypatch, tmp_path):
    """Real paramiko client (deploy_via_ssh) against a real asyncssh
    SFTP server whose key IS provisioned in the temp HOME's
    known_hosts → bytes land on disk. This is the sandbox stand-in
    for the iter367 real-sshd tests (no sshd binary here)."""
    asyncssh = pytest.importorskip("asyncssh")

    port = _free_port()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    pub = host_key.export_public_key().decode().strip()
    client_home = tmp_path / "client_home"
    (client_home / ".ssh").mkdir(parents=True)
    (client_home / ".ssh" / "known_hosts").write_text(
        f"[127.0.0.1]:{port} {pub}\n")
    monkeypatch.setenv("HOME", str(client_home))

    srv_root = tmp_path / "srv"
    srv_root.mkdir()

    class _SFTPServer(asyncssh.SFTPServer):
        def __init__(self, chan):
            super().__init__(chan, chroot=str(srv_root))

    class _PasswordServer(asyncssh.SSHServer):
        def password_auth_supported(self):
            return True

        def validate_password(self, username, password):
            return password == "sftppass"

    server = await asyncssh.create_server(
        _PasswordServer, "127.0.0.1", port, server_host_keys=[host_key],
        sftp_factory=_SFTPServer)
    try:
        res = await _run_blocking_deploy_via_ssh(
            host="127.0.0.1", port=port, user="deploy",
            password="sftppass", remote_dir="/deploys",
            files={"app/main.py": b"live-sftp-payload"})
    finally:
        server.close()
        await server.wait_closed()
    assert res["ok"] is True, res
    assert res["uploaded"] == 1
    assert (srv_root / "deploys" / "app" / "main.py").read_bytes() == \
        b"live-sftp-payload"


@pytest.mark.asyncio
async def test_deploy_via_ssh_live_untrusted_host(monkeypatch, tmp_path):
    """Same live SFTP server, empty HOME: RejectPolicy refuses the
    unknown host → ok=False, refusal in `fatal`."""
    asyncssh = pytest.importorskip("asyncssh")

    port = _free_port()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))

    srv_root = tmp_path / "srv"
    srv_root.mkdir()

    class _SFTPServer(asyncssh.SFTPServer):
        def __init__(self, chan):
            super().__init__(chan, chroot=str(srv_root))

    class _PasswordServer(asyncssh.SSHServer):
        def password_auth_supported(self):
            return True

        def validate_password(self, username, password):
            return password == "sftppass"

    server = await asyncssh.create_server(
        _PasswordServer, "127.0.0.1", port, server_host_keys=[host_key],
        sftp_factory=_SFTPServer)
    try:
        res = await _run_blocking_deploy_via_ssh(
            host="127.0.0.1", port=port, user="deploy",
            password="sftppass", remote_dir="/deploys",
            files={"app/main.py": b"live-sftp-payload"})
    finally:
        server.close()
        await server.wait_closed()
    assert res["ok"] is False, res
    assert "not found in known_hosts" in (res.get("fatal") or ""), res
    assert res["uploaded"] == 0
