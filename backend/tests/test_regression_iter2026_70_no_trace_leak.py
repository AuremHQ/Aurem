"""tests/test_regression_iter2026_70_no_trace_leak.py

Security audit Phase 4.7: two paths shipped Python tracebacks (file paths,
line numbers, exception text) to the client / persisted them in a row an admin
UI renders:

  * routers/fix_pipeline.py::_run_bulk_job — SSE `job-error` event carried
    `traceback=format_exc(limit=4)` plus the raw exception text, and the closed
    job's message embedded `str(e)[:200]`;
  * services/db_backup.py::run_backup — `error_str` (returned AND written to the
    backup history row) contained `repr(e)` and the full traceback.

Fix: log the full trace server-side, emit/store/return a generic error only.
"""
from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.asyncio

SECRET_PATH = "/app/backend/services/very_secret_module.py"
BOOM = f"boom at {SECRET_PATH} line 42 password=hunter2"


def _assert_clean(blob: str) -> None:
    text = str(blob)
    assert "Traceback" not in text and "File \"" not in text
    assert SECRET_PATH not in text and "very_secret_module" not in text
    assert "hunter2" not in text and "line 42" not in text


# ── fix_pipeline SSE ──────────────────────────────────────────────────
class _ProjCol:
    @staticmethod
    async def find_one(*a, **k):
        return None   # no preview_url -> no before/after capture


class _BulkDb:
    cto_projects = _ProjCol()


async def test_bulk_job_crash_emits_a_generic_job_error(monkeypatch, caplog):
    from routers import fix_pipeline as fp
    from services import fix_job_manager as fjm

    emitted: list[tuple] = []
    closed: list[dict] = []

    def poisoned(_findings):
        raise RuntimeError(BOOM)

    async def _noop(*a, **k):
        return None

    async def _close(db, job_id, **kw):
        closed.append(kw)

    monkeypatch.setattr(fp, "_interleave_by_severity", poisoned)
    monkeypatch.setattr(fp, "_release_all_bulk_holds", _noop)
    monkeypatch.setattr(fp, "emit_notification", _noop)
    monkeypatch.setattr(fjm, "emit", lambda job_id, event, **kw: emitted.append((event, kw)))
    monkeypatch.setattr(fjm, "close", _close)

    with caplog.at_level(logging.ERROR):
        await fp._run_bulk_job(job_id="j1", db=_BulkDb(), user={"user_id": "u1"}, project_id="p1",
                               findings=[{"id": "f1", "rule_id": "x", "file": "a.py"}], is_unlim=False)

    errors = [kw for ev, kw in emitted if ev == "job-error"]
    assert errors, "a crash must still emit a job-error event"
    for kw in errors:
        _assert_clean(kw)
        assert "traceback" not in kw
        assert kw["ok"] is False and kw["reason"] == "worker_exception"
        assert kw["message"]
    assert closed and closed[-1]["status"] == "failed"
    _assert_clean(closed[-1])
    # the full detail is still available to operators, in the server log
    assert any("very_secret_module" in (r.getMessage() + (str(r.exc_info[1]) if r.exc_info else "")) for r in caplog.records)


# ── db_backup ─────────────────────────────────────────────────────────
class _Col:
    def __init__(self):
        self.docs: list[dict] = []

    async def insert_one(self, doc, *a, **k):
        self.docs.append(dict(doc))

    async def update_one(self, flt, upd, *a, **k):
        self.docs.append({"filter": flt, "update": upd})

    async def delete_many(self, *a, **k):
        return None

    def find(self, *a, **k):
        return self

    def sort(self, *a, **k):
        return self

    async def to_list(self, *a, **k):
        return []


class _Db:
    def __init__(self):
        self.cols: dict[str, _Col] = {}

    def __getitem__(self, name):
        return self.cols.setdefault(name, _Col())

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


async def test_failed_backup_returns_and_stores_a_generic_error(monkeypatch):
    from services import db_backup

    db = _Db()
    stored: list[dict] = []

    async def _write_history(_db, **kw):
        stored.append(kw)

    async def _boom(*a, **k):
        raise RuntimeError(BOOM)

    monkeypatch.setattr(db_backup, "_write_history", _write_history)
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
    monkeypatch.setenv("DB_NAME", "aurem_test")
    for key in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT"):
        monkeypatch.setenv(key, "test-placeholder")
    monkeypatch.setattr(db_backup, "_dump_db_to_gzip_file", _boom)
    monkeypatch.setattr(db_backup, "_capture_sentry", lambda *a, **k: None)

    result = await db_backup.run_backup(db)

    assert result["ok"] is False
    _assert_clean(result["error"])
    assert stored and stored[-1]["status"] == "failed"
    _assert_clean(stored[-1]["error"])
    assert result["error"]  # not empty: the operator still sees that it failed
