"""tests/test_regression_iter2026_52_part2c_c4_bulk_start_fail_closed.py

Part 2c C4(a) [Low] — `reconcile_findings_against_stored_rows` and
`drop_unverified_findings` (services/finding_scope.py) both fail OPEN
on a Mongo query exception — a deliberate, documented contract for
those two functions as general-purpose reconciliation/best-effort
helpers. But their ONLY caller is `routers/fix_pipeline.py::
start_bulk_fix`, and for THAT specific path a Mongo outage meant the
request silently proceeded with un-reconciled, unverified findings
straight into billing (`assert_can_fix`) and the LLM pipeline — never
surfacing the outage to the caller at all.

Fix: `start_bulk_fix` now calls both helpers in `strict=True` mode —
a Mongo error there raises back out to the router, which turns it
into a clean, retryable 503 BEFORE any wallet hold, task-quota debit,
or worker is started. The helpers' own default (`strict=False`,
fail-open) behavior is UNCHANGED and still covered by test_regression
_iter2026_46's existing tests — this is an additive, opt-in mode for
the one caller that needed fail-closed semantics.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


class _RaisingFindingsColl:
    """Simulates a Mongo outage on the VERY FIRST query the bulk-
    start path makes against cto_open_findings — `.find()` returns a
    cursor synchronously (real Motor shape); the outage surfaces when
    that cursor is awaited via `.to_list()`."""
    def find(self, *a, **k):
        class _RaisingCursor:
            async def to_list(self, length=None):
                raise RuntimeError("simulated mongo outage")
        return _RaisingCursor()


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []

    async def find_one(self, filt, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


class _FakeDB:
    def __init__(self):
        self.cto_open_findings = _RaisingFindingsColl()
        self.dev_users = _FakeColl()
        self.cto_projects = _FakeColl([{"project_id": "p1", "user_id": "u1"}])
        self.fix_jobs = _FakeColl()

    def __getattr__(self, name):
        return _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


def _mock_gate(monkeypatch, fp, user_id="u1"):
    async def _fake_gate(authorization, level, db=None):
        caller = {"user_id": user_id, "is_admin": False}
        return caller, {"user_id": user_id}
    monkeypatch.setattr(fp, "_gate", _fake_gate)


class TestC4BulkStartFailsClosedOnMongoError:
    async def test_mongo_outage_during_reconcile_returns_503_not_a_silent_proceed(self, monkeypatch):
        import routers.fix_pipeline as fp

        db = _FakeDB()
        _mock_gate(monkeypatch, fp)
        monkeypatch.setattr(fp, "get_db", lambda: db)

        billing_called = {"hit": False}

        async def _should_never_run(*a, **k):
            billing_called["hit"] = True
            return {"is_unlimited": False}
        monkeypatch.setattr(fp, "assert_can_fix", _should_never_run)

        wallet_called = {"hit": False}

        async def _wallet_should_never_run(*a, **k):
            wallet_called["hit"] = True
        monkeypatch.setattr("services.wallet.assert_wallet", _wallet_should_never_run)

        with pytest.raises(HTTPException) as exc:
            await fp.start_bulk_fix(
                body={"project_id": "p1", "findings": [
                    {"id": "f1", "rule_id": "r1", "file": "a.py", "line": 1, "severity": "high"},
                ]},
                authorization="Bearer x",
            )

        assert exc.value.status_code == 503
        detail = exc.value.detail
        if isinstance(detail, dict):
            assert detail.get("retryable") is True
        assert billing_called["hit"] is False, (
            "C4 FIX REQUIRED: a Mongo outage during reconciliation must "
            "never let the request reach billing/quota at all."
        )
        assert wallet_called["hit"] is False, (
            "C4 FIX REQUIRED: a Mongo outage during reconciliation must "
            "never let the request reach the wallet hold."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
