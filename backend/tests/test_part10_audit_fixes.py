"""tests/test_part10_audit_fixes.py — Part X (ORA audit) fixes.

Proves the Part X fixes in:
  services/fix_job_manager.py    (1: _gc evicts closed jobs only;
                                  4: ledger failures recorded on the job)
  routers/fix_pipeline.py        (2: hold release on ANY assert_wallet
                                  error; 3: find_one inside the worker
                                  try:; 4: record_fixed surfaced;
                                  11: _verify_commit_exists rename)
  services/fixed_findings.py     (4: record_fixed logs + returns False)
  core/parliament/breaker.py     (5: record_neutral releases a probe;
                                  8: lock removed, stats()/record_* read
                                  through the .state property)
  core/parliament/llm_call.py    (5: cost-cap releases the probe;
                                  6: temperature threaded, TypeError
                                  retry gone; 7: non-dict meta guard)
  services/llm/_meta.py          (6: real temperature parameter)
  services/admin_error_autofix.py (9: isinstance guards on result)
  routers/vanguard_ci.py         (10: malformed payload coercion)

Deliberately NOT covered here (verifier: WRONG-or-STALE, not built):
  2(b-iii) _guardian_rescan returning None — display-only, no causal
  path to resurrected findings; 2(c) as a behaviour fix — both call
  sites already pass the effective dict (fix 11 is the rename only).

How it runs
-----------
The backend's full dependency tree is not installed in every
sandbox, so the modules under test are imported with lightweight
stub packages installed in sys.modules for the import only — the
REAL module files are always loaded from this backend/ tree, and
any pre-existing sys.modules entries are restored afterwards.
Where the real dependency is importable (fastapi, httpx) the real
one is used.  Run from backend/:
    python -m pytest tests/test_part10_audit_fixes.py -v
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
import time
import types
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]


# ─── Fake Mongo ────────────────────────────────────────────────────
class _FakeResult:
    def __init__(self, modified_count=0, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _FakeColl:
    """Minimal async Mongo collection fake: find_one / update_one
    with $set (and $inc), plus optional fault injection."""

    def __init__(self, rows=None, *, find_one_impl=None, update_one_impl=None):
        self.rows = list(rows or [])
        self.find_one_impl = find_one_impl
        self.update_one_impl = update_one_impl
        self.find_one_queries: list[dict] = []
        self.update_calls: list[tuple] = []

    @staticmethod
    def _matches(row: dict, query: dict) -> bool:
        return all(row.get(k) == v for k, v in query.items()
                   if not isinstance(v, dict))

    async def find_one(self, query, projection=None):
        self.find_one_queries.append(dict(query))
        if self.find_one_impl is not None:
            return await self.find_one_impl(query, projection)
        for r in self.rows:
            if self._matches(r, query):
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        self.update_calls.append((dict(query), update, upsert))
        if self.update_one_impl is not None:
            return await self.update_one_impl(query, update, upsert)
        for r in self.rows:
            if self._matches(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                for k, v in (update.get("$inc") or {}).items():
                    r[k] = (r.get(k) or 0) + v
                return _FakeResult(modified_count=1)
        if upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            doc.update(update.get("$set") or {})
            self.rows.append(doc)
            return _FakeResult(modified_count=0, upserted_id="fake-id")
        return _FakeResult(modified_count=0)


class _FakeDB:
    def __init__(self):
        self.fix_jobs = _FakeColl()
        self.fixed_findings = _FakeColl()
        self.cto_projects = _FakeColl()
        self.frontend_errors = _FakeColl()
        self.vanguard_ci_findings = _FakeColl()

    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll


# ─── Stub helpers ──────────────────────────────────────────────────
def _mod(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _pkg(name: str, path: Path) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__path__ = [str(path)]  # type: ignore[attr-defined]
    return m


class _FakeSpan:
    def __init__(self):
        self.metadata: dict = {}
        self.errors: list = []
        self.output = None

    def set_metadata(self, md):
        self.metadata.update(md or {})

    def record_error(self, err):
        self.errors.append(err)

    def set_output(self, out):
        self.output = out


@contextmanager
def _fake_trace(*args, **kwargs):
    yield _FakeSpan()


@asynccontextmanager
async def _null_async_cm(*args, **kwargs):
    yield None


@asynccontextmanager
async def _claim_recording_cm(*args, **kwargs):
    yield False


def _load_modules():
    """Import the REAL modules under test from this backend/ tree,
    with stub packages standing in for sibling modules that are not
    seeded here.  Pre-existing sys.modules entries are restored
    afterwards; the real submodules loaded stay cached (they ARE the
    production files, just resolved via the stub parents' __path__)."""
    saved: dict[str, types.ModuleType | None] = {}

    def _put(name, mod):
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    async def _noop(*args, **kwargs):
        return None

    # --- cto_services (auth + db) ---
    _put("cto_services", _pkg("cto_services", BACKEND / "cto_services"))
    _put("cto_services.auth", _mod(
        "cto_services.auth", current_dev=AsyncMock(), create_token=lambda *a, **k: "tok"))
    _put("cto_services.db", _mod("cto_services.db", get_db=lambda: None))

    # --- services package: real files resolve via __path__; the rest are stubs ---
    _put("services", _pkg("services", BACKEND / "services"))
    _put("services.ora_fix_learning", _mod(
        "services.ora_fix_learning", record_fix_outcome=AsyncMock()))
    _put("services.finding_fix_applier", _mod(
        "services.finding_fix_applier",
        apply_file_fixes=AsyncMock(), revert_batch=AsyncMock(),
        revert_finding_fix=AsyncMock()))
    _put("services.notifications", _mod(
        "services.notifications", emit_notification=AsyncMock()))
    _put("services.rate_limiter", _mod(
        "services.rate_limiter", check_rate_limit_async=AsyncMock()))
    _put("services.scan_fix_quota", _mod(
        "services.scan_fix_quota",
        ALL_FIX_TOOLS={"health-scan", "vanguard", "bug-hunt"},
        assert_can_fix=AsyncMock(return_value={"is_unlimited": True}),
        get_fix_quota=AsyncMock(return_value={"is_unlimited": True}),
        record_scan_fixes=AsyncMock()))
    _put("services.team_permissions", _mod(
        "services.team_permissions", check_permission=AsyncMock(
            return_value={"effective_account_id": "acct-1"})))

    # --- routers package: real files resolve via __path__ ---
    _put("routers", _pkg("routers", BACKEND / "routers"))

    # --- core.parliament: real files resolve via __path__ ---
    _put("core", _pkg("core", BACKEND / "core"))
    _put("core.parliament", _pkg("core.parliament", BACKEND / "core" / "parliament"))
    _put("core.observability", _mod(
        "core.observability", trace_llm=_fake_trace))

    mods = {
        "fjm": importlib.import_module("services.fix_job_manager"),
        "fixed_findings": importlib.import_module("services.fixed_findings"),
        "fp": importlib.import_module("routers.fix_pipeline"),
        "vc": importlib.import_module("routers.vanguard_ci"),
        "breaker_mod": importlib.import_module("core.parliament.breaker"),
        "llm_call": importlib.import_module("core.parliament.llm_call"),
        "autofix": _load_by_path(
            "part10_admin_error_autofix",
            BACKEND / "services" / "admin_error_autofix.py"),
        "meta": _load_by_path(
            "part10_llm_meta", BACKEND / "services" / "llm" / "_meta.py"),
    }

    # Restore any pre-existing entries for the stub PARENTS so other
    # test files in the same session see the real packages again.
    for name, original in saved.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
    return mods


def _load_by_path(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_M = _load_modules()
fjm = _M["fjm"]
fixed_findings = _M["fixed_findings"]
fp = _M["fp"]
vc = _M["vc"]
breaker_mod = _M["breaker_mod"]
llm_call = _M["llm_call"]
autofix = _M["autofix"]
meta_mod = _M["meta"]

Breaker = breaker_mod.ParliamentCircuitBreaker

_FINDINGS_2 = [
    {"id": "f1", "rule_id": "r1", "file": "a.py", "line": 1, "severity": "critical"},
    {"id": "f2", "rule_id": "r2", "file": "b.py", "line": 2, "severity": "high"},
]


@pytest.fixture(autouse=True)
def _clear_jobs():
    fjm._JOBS.clear()
    yield
    fjm._JOBS.clear()


# ─── Helpers for the fix_pipeline tests ────────────────────────────
def _services_stub(monkeypatch, **leaves):
    """Install the services stub package + leaf stubs for the duration
    of one test (monkeypatch undoes them).  Needed because lazy
    `from services.<leaf> import ...` calls inside the production
    code resolve through sys.modules at CALL time."""
    monkeypatch.setitem(sys.modules, "services", _pkg("services", BACKEND / "services"))
    for name, mod in leaves.items():
        monkeypatch.setitem(sys.modules, f"services.{name}", mod)


def _wallet_stub(monkeypatch, *, fail_on_call: int | None = None):
    """Stub services.wallet.  assert_wallet succeeds except on the
    Nth call (1-based) when fail_on_call is set; finalize_hold just
    records.  Returns (module, calls) with calls["hold"] /
    calls["finalize"] lists."""
    calls: dict[str, list] = {"hold": [], "finalize": []}
    state = {"n": 0}

    async def assert_wallet(db, *, user_id, action, run_id, idempotency_key):
        state["n"] += 1
        calls["hold"].append(run_id)
        if fail_on_call is not None and state["n"] == fail_on_call:
            raise RuntimeError("simulated wallet DB outage")

    async def finalize_hold(db, *, user_id, action, run_id, ok, reason=""):
        calls["finalize"].append(
            {"run_id": run_id, "ok": ok, "reason": reason})

    mod = _mod("services.wallet", assert_wallet=assert_wallet,
               finalize_hold=finalize_hold)
    _services_stub(monkeypatch, wallet=mod)
    return mod, calls


def _finding_scope_stub(monkeypatch):
    async def _passthrough(db, **kw):
        return kw.get("findings") or []

    async def _resolve(db, **kw):
        return kw.get("findings") or []

    mod = _mod("services.finding_scope",
               resolve_findings_by_ids=_resolve,
               reconcile_findings_against_stored_rows=_passthrough,
               drop_unverified_findings=_passthrough)
    monkeypatch.setitem(sys.modules, "services.finding_scope", mod)
    return mod


# ─── Fix 1 — _gc() evicts closed jobs only ────────────────────────
@pytest.mark.asyncio
async def test_gc_evicts_oldest_closed_not_running():
    now = time.time()
    for i in range(1000):
        fjm._JOBS[f"closed_{i}"] = {"closed_at": now - 100 + i}
    running_id = "running_1"
    fjm._JOBS[running_id] = {"closed_at": None}
    new_id = await fjm.create_job(db=None, user_id="u1", kind="bulk", total=1)
    assert running_id in fjm._JOBS, "a running job must survive _gc()"
    assert "closed_0" not in fjm._JOBS, "the oldest closed job is evicted first"
    assert new_id in fjm._JOBS
    assert len(fjm._JOBS) == 1001  # 999 closed + 1 running + 1 new


@pytest.mark.asyncio
async def test_gc_with_only_running_jobs_evicts_nothing():
    for i in range(1001):
        fjm._JOBS[f"running_{i}"] = {"closed_at": None}
    new_id = await fjm.create_job(db=None, user_id="u1", kind="bulk", total=1)
    assert len(fjm._JOBS) == 1002, (
        "above the cap with no closed jobs at all, _gc() must evict "
        "nothing rather than kill a live job")
    assert new_id in fjm._JOBS


# ─── Fix 2 — start_bulk_fix releases holds on ANY exception ───────
@pytest.mark.asyncio
async def test_start_bulk_fix_non_http_exception_releases_holds_and_closes_job(monkeypatch):
    db = _FakeDB()
    _, wallet_calls = _wallet_stub(monkeypatch, fail_on_call=2)
    _finding_scope_stub(monkeypatch)
    monkeypatch.setitem(sys.modules, "services.fix_entitlement", _mod(
        "services.fix_entitlement", assert_fix_entitled=AsyncMock()))

    async def fake_gate(auth, level):
        return {"user_id": "u1"}, {"user_id": "u1", "tier": "team"}
    monkeypatch.setattr(fp, "_gate", fake_gate)
    monkeypatch.setattr(fp, "get_db", lambda: db)
    created = {}

    def _no_task(coro):
        created["called"] = True
        coro.close()
        return None
    monkeypatch.setattr(fp.asyncio, "create_task", _no_task)

    with pytest.raises(HTTPException) as exc_info:
        await fp.start_bulk_fix(
            body={"project_id": "p1", "findings": _FINDINGS_2},
            authorization="Bearer x",
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "wallet_hold_failed"
    job_id = exc_info.value.detail["job_id"]
    assert not created, "the worker must never launch after a hold failure"
    # Exactly the one hold taken before the failure is released.
    assert len(wallet_calls["hold"]) == 2
    assert len(wallet_calls["finalize"]) == 1
    assert wallet_calls["finalize"][0]["ok"] is False
    assert wallet_calls["finalize"][0]["reason"] == "bulk_hold_aborted"
    assert wallet_calls["finalize"][0]["run_id"] == wallet_calls["hold"][0]
    # The job row is closed failed, not stuck "running".
    row = next(r for r in db.fix_jobs.rows if r["job_id"] == job_id)
    assert row["status"] == "failed"
    assert fjm.get_summary(job_id)["status"] == "failed"


# ─── Fix 3 — _run_bulk_job find_one inside the outer try: ─────────
@pytest.mark.asyncio
async def test_run_bulk_job_find_one_failure_releases_holds_and_closes_job(monkeypatch):
    db = _FakeDB()
    _, wallet_calls = _wallet_stub(monkeypatch)
    notifications = AsyncMock()
    monkeypatch.setattr(fp, "emit_notification", notifications)

    async def _boom(query, projection=None):
        raise RuntimeError("simulated Mongo outage")
    db.cto_projects.find_one_impl = _boom

    jid = await fjm.create_job(db=db, user_id="u1", kind="bulk", total=2,
                               project_id="p1", findings=_FINDINGS_2)
    await fp._run_bulk_job(job_id=jid, db=db, user={"user_id": "u1"},
                           project_id="p1", findings=_FINDINGS_2,
                           is_unlim=True)
    # Holds released for EVERY finding, job closed failed, ship_failed sent.
    assert len(wallet_calls["finalize"]) == 2
    assert all(c["ok"] is False for c in wallet_calls["finalize"])
    assert all(c["reason"] == "bulk_job_crashed_or_cancelled"
               for c in wallet_calls["finalize"])
    assert {c["run_id"] for c in wallet_calls["finalize"]} == {
        fp._wallet_run_id(jid, f) for f in _FINDINGS_2}
    assert fjm.get_summary(jid)["status"] == "failed"
    notifications.assert_awaited_once()
    assert notifications.await_args.kwargs.get("type") == "ship_failed"


# ─── Fix 4 — record_fixed failures are surfaced ───────────────────
@pytest.mark.asyncio
async def test_record_fixed_returns_false_and_logs_on_db_failure(caplog):
    db = _FakeDB()

    async def _boom(query, update, upsert=False):
        raise RuntimeError("simulated ledger outage")
    db.fixed_findings.update_one_impl = _boom
    with caplog.at_level(logging.WARNING):
        ok = await fixed_findings.record_fixed(
            db, user_id="u1", project_id="p1", finding=_FINDINGS_2[0],
            commit_sha="abc", html_url="https://x", tool="health-scan")
    assert ok is False, "a failed ledger write must report False, not swallow"
    assert "record_fixed failed" in caplog.text


@pytest.mark.asyncio
async def test_record_fixed_success_still_returns_true():
    db = _FakeDB()
    ok = await fixed_findings.record_fixed(
        db, user_id="u1", project_id="p1", finding=_FINDINGS_2[0],
        commit_sha="abc", html_url="https://x", tool="health-scan")
    assert ok is True
    assert len(db.fixed_findings.rows) == 1


@pytest.mark.asyncio
async def test_run_bulk_job_ledger_failure_recorded_but_fix_still_ok(monkeypatch, caplog):
    db = _FakeDB()
    _, wallet_calls = _wallet_stub(monkeypatch)
    notifications = AsyncMock()
    monkeypatch.setattr(fp, "emit_notification", notifications)
    _services_stub(
        monkeypatch,
        loop_token_ledger=_mod(
            "services.loop_token_ledger",
            llm_surface_context=_null_async_cm),
        brain_gateway=_mod(
            "services.brain_gateway",
            attach_outcome_for_task=AsyncMock()),
        # _compute_diff_lines imports this at call time.
        secret_masking=_mod(
            "services.secret_masking",
            redact_secrets_in_text=lambda text: text),
    )

    async def _boom(query, update, upsert=False):
        raise RuntimeError("simulated ledger outage")
    db.fixed_findings.update_one_impl = _boom

    async def fake_apply_file(*, db, user, project_id, file, findings):
        return {"ok": True, "file": file, "commit_sha": "abc1234",
                "full_sha": "abc1234" * 5,
                "html_url": "https://x/commit/abc1234",
                "pr_url": "https://x/pull/1", "branch": "aurem/fixes-x",
                "batch_id": "fb_1",
                "results": [{"finding_id": "f1", "rule_id": "r1",
                             "file": file, "status": "resolved"}]}
    monkeypatch.setattr(fp, "apply_file_fixes", fake_apply_file)
    monkeypatch.setattr(fp, "_verify_commit_exists",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(fp, "_guardian_rescan",
                        lambda *a: {"category": "x", "clean": True,
                                    "remaining_in_file": 0})

    jid = await fjm.create_job(db=db, user_id="u1", kind="bulk", total=1,
                               project_id="p1", findings=_FINDINGS_2[:1])
    with caplog.at_level(logging.WARNING):
        await fp._run_bulk_job(job_id=jid, db=db, user={"user_id": "u1"},
                               project_id="p1", findings=_FINDINGS_2[:1],
                               is_unlim=True)
    summary = fjm.get_summary(jid)
    assert summary["status"] == "done", "a ledger error must not fail the fix"
    assert summary["completed"] == 1
    assert summary["ledger_failures"], "the failure must be recorded on the job"
    assert summary["ledger_failures"][0]["finding_id"] == "f1"
    assert "record_fixed failed" in caplog.text
    # The fix itself settled (spent), not released.
    assert wallet_calls["finalize"][0]["ok"] is True
    notifications.assert_awaited_once()
    assert notifications.await_args.kwargs.get("type") == "ship_done"


# ─── Fix 11 — _verify_commit_exists takes the effective dict ──────
def test_verify_commit_exists_param_is_effective():
    import inspect
    params = inspect.signature(fp._verify_commit_exists).parameters
    assert "effective" in params, "parameter must be renamed user -> effective"
    assert "user" not in params


@pytest.mark.asyncio
async def test_verify_commit_exists_filters_by_effective_account_id():
    db = _FakeDB()
    db.cto_projects.rows.append({
        "project_id": "p1", "user_id": "acct-1",
        "github_owner": "o", "github_repo": "r",
    })
    # No commit SHA: early False without touching GitHub.
    assert await fp._verify_commit_exists(
        db=db, effective={"user_id": "acct-1"}, project_id="p1",
        full_sha=None) is False
    # Project row missing for this account id: None, and the query
    # proves the filter used the EFFECTIVE id.
    assert await fp._verify_commit_exists(
        db=db, effective={"user_id": "acct-2"}, project_id="p1",
        full_sha="deadbeef") is None
    assert db.cto_projects.find_one_queries[-1] == {
        "project_id": "p1", "user_id": "acct-2"}


# ─── Fix 5 + 8 — breaker: neutral record, stats(), lock ───────────
def _half_open_breaker(monkeypatch_llm=None):
    """A breaker driven to half_open with its probe claimed."""
    b = Breaker()
    b.record_failure()
    b.record_failure()
    b.record_failure()
    assert b._state == "open"
    b._opened_at = time.monotonic() - (Breaker.COOLDOWN_SECONDS + 1)
    assert b.should_attempt() is True, "first attempt after cooldown claims the probe"
    assert b.should_attempt() is False, "probe is held until recorded"
    return b


def _llm_call_stubs(monkeypatch, call_llm_with_meta):
    _services_stub(
        monkeypatch,
        llm=_mod("services.llm", call_llm_with_meta=call_llm_with_meta),
        loop_token_ledger=_mod(
            "services.loop_token_ledger",
            llm_surface_context=_null_async_cm),
    )


@pytest.mark.asyncio
async def test_cost_cap_releases_half_open_probe(monkeypatch):
    b = _half_open_breaker()
    monkeypatch.setattr(llm_call, "_GLOBAL_BREAKER", b)
    failures_before = b._consec_failures

    async def fake_llm(**kwargs):
        return {"ok": False, "error_code": "COST_CAP_REACHED", "content": ""}
    _llm_call_stubs(monkeypatch, fake_llm)

    content, _ms, err = await llm_call._llm_call_protected_impl(
        system="s", user="u", max_tokens=100, mode="chat", review_mode="swift")
    assert err == "cost_cap_reached"
    assert b.should_attempt() is True, (
        "after a cost-cap probe the breaker must allow the next attempt")
    assert b.state == "half_open", "neutral must not close the breaker"
    assert b._consec_failures == failures_before, (
        "a cost cap is not a provider failure and must not count toward it")


@pytest.mark.asyncio
async def test_cost_cap_does_not_wedge_self_heal_shape(monkeypatch):
    """Same wedge, from the self-heal side: its only LLM call sits
    behind should_attempt(), so a stuck probe would return
    circuit_open forever.  After the fix the gate opens again."""
    b = _half_open_breaker()
    monkeypatch.setattr(llm_call, "_GLOBAL_BREAKER", b)

    async def fake_llm(**kwargs):
        return {"ok": False, "error_code": "COST_CAP_REACHED", "content": ""}
    _llm_call_stubs(monkeypatch, fake_llm)
    await llm_call._llm_call_protected_impl(
        system="s", user="u", max_tokens=100, mode="chat", review_mode="swift")
    assert b.should_attempt() is True


def test_stats_reports_half_open_after_cooldown():
    b = Breaker()
    b.record_failure()
    b.record_failure()
    b.record_failure()
    assert b._state == "open"
    b._opened_at = time.monotonic() - (Breaker.COOLDOWN_SECONDS + 1)
    assert b.stats()["state"] == "half_open", (
        "stats() must read through the .state property, not the raw field")


def test_record_failure_after_cooldown_is_probe_failure():
    b = Breaker()
    b.record_failure()
    b.record_failure()
    b.record_failure()
    b._opened_at = time.monotonic() - (Breaker.COOLDOWN_SECONDS + 1)
    before = time.monotonic()
    b.record_failure()
    assert b._state == "open", "a post-cooldown failure re-opens the breaker"
    assert b._opened_at >= before, "with a fresh cooldown"
    assert b._half_open_probe is False


def test_breaker_lock_removed():
    b = Breaker()
    assert not hasattr(b, "_lock"), (
        "the unused asyncio.Lock is removed: the state methods are "
        "synchronous and already atomic under asyncio")


# ─── Fix 6 — temperature threaded, TypeError retry gone ────────────
@pytest.mark.asyncio
async def test_temperature_reaches_llm_once_without_typeerror_roundtrip(monkeypatch):
    b = Breaker()
    monkeypatch.setattr(llm_call, "_GLOBAL_BREAKER", b)
    calls: list[dict] = []

    async def fake_llm(**kwargs):
        calls.append(dict(kwargs))
        return {"ok": True, "content": "hello", "provider": "p",
                "model": "m", "total_tokens": 5}
    _llm_call_stubs(monkeypatch, fake_llm)

    content, _ms, err = await llm_call._llm_call_protected_impl(
        system="s", user="u", max_tokens=100, mode="chat",
        review_mode="swift", temperature=0.7)
    assert (content, err) == ("hello", None)
    assert len(calls) == 1, "exactly one LLM call — no TypeError round-trip"
    assert calls[0]["temperature"] == 0.7


@pytest.mark.asyncio
async def test_internal_typeerror_is_not_retried(monkeypatch):
    b = Breaker()
    monkeypatch.setattr(llm_call, "_GLOBAL_BREAKER", b)
    calls: list[dict] = []

    async def fake_llm(**kwargs):
        calls.append(dict(kwargs))
        raise TypeError("boom inside provider")
    _llm_call_stubs(monkeypatch, fake_llm)

    _content, _ms, err = await llm_call._llm_call_protected_impl(
        system="s", user="u", max_tokens=100, mode="chat",
        review_mode="swift", temperature=0.7)
    assert len(calls) == 1, "an internal TypeError must not trigger a retry"
    assert err.startswith("TypeError"), "it is recorded as a failure, not masked"
    assert b._consec_failures == 1


def _meta_stubs(monkeypatch, glm_calls):
    """Stubs for the services.llm lazy imports inside _meta.py plus
    the impl-chain imports, so call_llm_with_meta can run end to end
    without any provider."""
    async def fake_glm(*, system, user, max_tokens, temperature, feature=None):
        glm_calls.append({"temperature": temperature})
        return "swift content"

    llm_pkg = _mod(
        "services.llm",
        _CLAUDE_MODES={"code"}, _CLAUDE_MODEL="claude-m",
        _GLM_MODEL="glm-m", _GLM_REVIEW_MODEL="glm-review-m",
        _LONGCAT_MODEL="longcat-m", LONGCAT_ENABLED=False,
        COUNCIL_B_GLM_ENABLED=False,
        _call_claude=AsyncMock(), _call_deepseek=AsyncMock(),
        _call_glm=fake_glm, _call_glm_review=AsyncMock(),
        _call_longcat=AsyncMock(), _deepseek_model="deepseek-m",
        _openrouter_key=lambda: None,
        cap_for=lambda mode: 1500,
        temperature_for=lambda mode: 0.99,
    )
    monkeypatch.setitem(sys.modules, "services", _pkg("services", BACKEND / "services"))
    monkeypatch.setitem(sys.modules, "services.llm", llm_pkg)
    monkeypatch.setitem(sys.modules, "services.loop_token_ledger", _mod(
        "services.loop_token_ledger",
        llm_user_context=_null_async_cm,
        llm_surface_context=_null_async_cm,
        current_loop_context=lambda: (None, None, None),
        current_surface_context=lambda: {}))
    monkeypatch.setitem(sys.modules, "services.brain_gateway", _mod(
        "services.brain_gateway",
        claim_recording=_claim_recording_cm,
        record_call=AsyncMock()))
    monkeypatch.setitem(sys.modules, "services.ora_chat_v2", _pkg(
        "services.ora_chat_v2", BACKEND / "services" / "ora_chat_v2"))
    monkeypatch.setitem(sys.modules, "services.ora_chat_v2.llm_client", _mod(
        "services.ora_chat_v2.llm_client", is_mock=lambda: False))
    monkeypatch.setitem(sys.modules, "services.langfuse_tracing", _mod(
        "services.langfuse_tracing",
        trace_llm_call=lambda **kw: _FakeTraceCall()))
    monkeypatch.setitem(sys.modules, "cto_services", _pkg(
        "cto_services", BACKEND / "cto_services"))
    monkeypatch.setitem(sys.modules, "cto_services.db", _mod(
        "cto_services.db", get_db=lambda: None))
    monkeypatch.setitem(sys.modules, "services.llm_cost_breaker", _mod(
        "services.llm_cost_breaker",
        assert_within_cap=AsyncMock(), record_cost=AsyncMock()))
    monkeypatch.setitem(sys.modules, "services.customer_cost_tracker", _mod(
        "services.customer_cost_tracker",
        estimate_tokens=lambda text: len(text or "") // 4))
    monkeypatch.setitem(sys.modules, "services.ora_chat", _pkg(
        "services.ora_chat", BACKEND / "services" / "ora_chat"))
    monkeypatch.setitem(sys.modules, "services.ora_chat.cost_tracker", _mod(
        "services.ora_chat.cost_tracker",
        compute_cost_usd=lambda *a, **k: 0.0))


class _FakeTraceCall:
    def __enter__(self):
        return {"success": lambda result: None}

    def __exit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_meta_temperature_threaded_end_to_end(monkeypatch):
    glm_calls: list[dict] = []
    _meta_stubs(monkeypatch, glm_calls)
    res = await meta_mod.call_llm_with_meta(
        "sys", "usr", mode="chat", review_mode="swift", temperature=0.7)
    assert res["temperature"] == 0.7
    assert glm_calls == [{"temperature": 0.7}], (
        "the caller's temperature must reach the provider call")
    glm_calls.clear()
    res = await meta_mod.call_llm_with_meta(
        "sys", "usr", mode="chat", review_mode="swift")
    assert res["temperature"] == 0.99, (
        "no temperature passed -> the mode-derived default is unchanged")
    assert glm_calls == [{"temperature": 0.99}]


def test_meta_signatures_accept_temperature():
    import inspect
    for fn in (meta_mod.call_llm_with_meta,
               meta_mod._call_llm_with_meta_impl,
               meta_mod._call_llm_with_meta_inner):
        assert "temperature" in inspect.signature(fn).parameters, fn.__name__


# ─── Fix 7 — non-dict meta handled ─────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("bad_meta", ["raw-string", ["x"], 5])
async def test_non_dict_meta_does_not_crash(monkeypatch, bad_meta):
    b = Breaker()
    monkeypatch.setattr(llm_call, "_GLOBAL_BREAKER", b)

    async def fake_llm(**kwargs):
        return bad_meta
    _llm_call_stubs(monkeypatch, fake_llm)

    content, _ms, err = await llm_call._llm_call_protected_impl(
        system="s", user="u", max_tokens=100, mode="chat", review_mode="swift")
    assert (content, err) == ("", "empty"), (
        "a truthy non-dict meta is an empty response, not an AttributeError")


# ─── Fix 9 — autofix isinstance guards ─────────────────────────────
def _autofix_db():
    db = _FakeDB()
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_result", ["some string", ["x"], 42])
async def test_autofix_non_dict_result_marks_failed_not_crash(monkeypatch, bad_result):
    db = _autofix_db()
    monkeypatch.setitem(sys.modules, "services", _pkg("services", BACKEND / "services"))
    monkeypatch.setitem(sys.modules, "services.orchestrator", _mod(
        "services.orchestrator",
        chat_with_tools=AsyncMock(return_value=bad_result)))

    await autofix.run_error_autofix(
        db, admin_user_id="admin1", jwt_token="tok", error_id="e1",
        oid="oid1", prompt="investigate")
    sets = [c[1]["$set"] for c in db.frontend_errors.update_calls]
    assert sets[-1]["autofix_status"] == "failed"
    # Discriminator vs the old crash path: the success-path update
    # ran (proposed_at set, no autofix_error), not the except branch.
    assert "autofix_proposed_at" in sets[-1]
    assert "autofix_error" not in sets[-1]

    await autofix.apply_error_autofix(
        db, admin_user_id="admin1", jwt_token="tok", error_id="e1",
        oid="oid1", proposal="do the fix")
    sets = [c[1]["$set"] for c in db.frontend_errors.update_calls]
    assert sets[-1]["autofix_status"] == "failed"
    assert "autofix_finished" in sets[-1]
    assert "autofix_error" not in sets[-1]


@pytest.mark.asyncio
async def test_autofix_dict_result_still_proposes_and_applies(monkeypatch):
    db = _autofix_db()
    monkeypatch.setitem(sys.modules, "services", _pkg("services", BACKEND / "services"))
    monkeypatch.setitem(sys.modules, "services.orchestrator", _mod(
        "services.orchestrator",
        chat_with_tools=AsyncMock(
            return_value={"ok": True, "content": "fix: patch the handler"})))
    await autofix.run_error_autofix(
        db, admin_user_id="admin1", jwt_token="tok", error_id="e1",
        oid="oid1", prompt="investigate")
    sets = [c[1]["$set"] for c in db.frontend_errors.update_calls]
    assert sets[-1]["autofix_status"] == "proposed"
    assert sets[-1]["autofix_proposal"] == "fix: patch the handler"
    await autofix.apply_error_autofix(
        db, admin_user_id="admin1", jwt_token="tok", error_id="e1",
        oid="oid1", proposal="fix: patch the handler")
    sets = [c[1]["$set"] for c in db.frontend_errors.update_calls]
    assert sets[-1]["autofix_status"] == "done"


# ─── Fix 10 — vanguard_ci malformed payloads ───────────────────────
def test_normalise_trufflehog_malformed_nested_values():
    out = vc._normalise_trufflehog([
        {"Verified": True, "DetectorName": "AWS", "SourceMetadata": "x",
         "Raw": "SECRET123456"},
        {"SourceMetadata": {"Data": "oops"}, "Raw": 12345},
        {"SourceMetadata": {"Data": {"Filesystem": {"file": 123, "line": "abc"}}}},
        {"SourceMetadata": {"Data": {"Filesystem": {"file": "a.py", "line": "42"}}}},
        {"SourceMetadata": {"Data": {"Filesystem": ["a.py"]}}},
        "junk-string-finding",
    ])
    assert len(out) == 5, "malformed nested values coerce; non-dict findings skip"
    assert out[0]["file"] == "" and out[0]["line"] == 0
    assert out[0]["redacted"] == "SECR…56"
    assert out[1]["file"] == "" and out[1]["redacted"] == ""
    assert out[2]["file"] == "" and out[2]["line"] == 0
    assert out[3]["file"] == "a.py" and out[3]["line"] == 42
    assert out[4]["file"] == ""


@pytest.mark.asyncio
async def test_ingest_malformed_payloads_never_500(monkeypatch):
    monkeypatch.setenv("AUREM_CI_INGEST_TOKEN", "test-token")
    db = _FakeDB()
    monkeypatch.setattr(vc, "get_db", lambda: db)
    auth = "Bearer test-token"

    # Non-str repo -> 400, not a 500 on .strip().
    with pytest.raises(HTTPException) as exc_info:
        await vc.ingest_ci_findings(
            {"repo": 123, "commit": "abc", "scanner": "trufflehog",
             "findings": []}, authorization=auth)
    assert exc_info.value.status_code == 400

    # Non-str scanner -> 400.
    with pytest.raises(HTTPException) as exc_info:
        await vc.ingest_ci_findings(
            {"repo": "o/r", "commit": "abc", "scanner": True,
             "findings": []}, authorization=auth)
    assert exc_info.value.status_code == 400

    # Non-list findings -> existing 400.
    with pytest.raises(HTTPException) as exc_info:
        await vc.ingest_ci_findings(
            {"repo": "o/r", "commit": "abc", "scanner": "trufflehog",
             "findings": "not-a-list"}, authorization=auth)
    assert exc_info.value.status_code == 400

    # Malformed nested finding + non-str optional fields -> 200, and
    # the good finding is still stored.
    res = await vc.ingest_ci_findings(
        {"repo": "o/r", "commit": "abc", "scanner": "trufflehog",
         "findings": [
             {"SourceMetadata": "x", "Raw": 999, "DetectorName": "AWS"},
             {"Verified": True, "DetectorName": "GH",
              "SourceMetadata": {"Data": {"Filesystem": {
                  "file": "s.py", "line": "7"}}}, "Raw": "tok123456789"},
         ],
         "run_url": 123, "triggered_by": {"actor": "bot"}},
        authorization=auth)
    assert res["ok"] is True
    assert res["stored"] == 2
    stored = db.vanguard_ci_findings.rows[0]
    assert stored["run_url"] == "" and stored["triggered_by"] == ""
    assert stored["findings"][1]["file"] == "s.py"
    assert stored["findings"][1]["line"] == 7
