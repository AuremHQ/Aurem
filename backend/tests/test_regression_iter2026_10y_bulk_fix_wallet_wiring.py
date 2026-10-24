"""tests/test_regression_iter2026_10y_bulk_fix_wallet_wiring.py

Master Prompt v2 Part 1.1 — N findings = N billable units in
routers/fix_pipeline.py::start_bulk_fix / _run_bulk_job.

Design: N independent per-finding wallet holds, all placed up front
(synchronously, before any work starts — same "clean 402 before any
spend" guarantee as every other fix surface), keyed by a stable
per-finding identity (`_wallet_run_id`) that survives
`_interleave_by_severity`'s reordering. Each finding settles on
success or releases on failure independently; a job that crashes or
is cancelled mid-flight releases every finding's hold regardless of
whether the loop ever reached it.

Uses a REAL local Mongo connection (wallet.py's atomic pipeline and
idempotent ledger dedup cannot be meaningfully proven against a
dict-based mock).
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)

FIX_PRICE = 12  # config/token_pricing.py::PRICE_TABLE["fix"]


def _uid(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def db():
    if not _live_available:
        pytest.skip("MONGO_URL/DB_NAME not set")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    database = client[DB_NAME]
    yield database
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})
        await db.wallet_ledger.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.fix_jobs.delete_many({"user_id": {"$regex": "^wallet_test_"}})
    from cto_services import db as _dbmod
    _dbmod.set_db(None)


_FINDINGS_2 = [
    {"id": "f1", "rule_id": "r1", "file": "a.py", "line": 1, "severity": "critical"},
    {"id": "f2", "rule_id": "r2", "file": "b.py", "line": 2, "severity": "high"},
]


async def _setup_user(db, tag, *, balance):
    from services import wallet
    u = _uid(tag)
    await db.dev_users.insert_one({
        "user_id": u, "tier": "team", "fix_entitlement": True,
        "tokens_remaining": 0,
    })
    if balance:
        await wallet.grant(db, user_id=u, bucket="purchased", amount=balance,
                           idempotency_key=f"grant:{u}", source="test")
    return u


class TestBulkFixWalletWiring:
    async def test_holds_all_findings_up_front(self, db, monkeypatch):
        from routers import fix_pipeline as fp
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = await _setup_user(db, "bulkhold", balance=100)

        async def fake_gate(auth, level):
            return {"user_id": u}, {"user_id": u, "tier": "team"}
        monkeypatch.setattr(fp, "_gate", fake_gate)
        monkeypatch.setattr(fp, "get_db", lambda: db)
        monkeypatch.setattr(fp.asyncio, "create_task", lambda coro: coro.close())
        from cto_services import db as _dbmod
        _dbmod.set_db(db)

        result = await fp.start_bulk_fix(
            body={"project_id": "p1", "findings": _FINDINGS_2}, authorization="Bearer x",
        )
        assert result["ok"] is True
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - 2 * FIX_PRICE, "both findings held up front"

    async def test_insufficient_balance_blocks_before_any_hold_persists(self, db, monkeypatch):
        from routers import fix_pipeline as fp
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        # Enough for exactly ONE finding, not two.
        u = await _setup_user(db, "bulkbroke", balance=FIX_PRICE)

        async def fake_gate(auth, level):
            return {"user_id": u}, {"user_id": u, "tier": "team"}
        monkeypatch.setattr(fp, "_gate", fake_gate)
        monkeypatch.setattr(fp, "get_db", lambda: db)
        from cto_services import db as _dbmod
        _dbmod.set_db(db)

        with pytest.raises(HTTPException) as exc_info:
            await fp.start_bulk_fix(
                body={"project_id": "p1", "findings": _FINDINGS_2}, authorization="Bearer x",
            )
        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["code"] == "tokens_exhausted"
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == FIX_PRICE, (
            "the one successful hold from the aborted up-front pass must be "
            "released — never left stuck for a request that never started"
        )

    async def test_partial_success_settles_winners_releases_losers(self, db, monkeypatch):
        from routers import fix_pipeline as fp
        from services import fix_job_manager as fjm
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = await _setup_user(db, "bulkpartial", balance=2 * FIX_PRICE)

        async def fake_apply(*, db, user, project_id, finding):
            if finding["id"] == "f1":
                return {"ok": True, "commit_sha": "abc1234", "full_sha": "abc1234" * 5,
                        "html_url": "https://x", "file": finding["file"],
                        "rule_id": finding["rule_id"], "message": "Fixed"}
            return {"ok": False, "error": "patch_did_not_resolve_finding"}
        monkeypatch.setattr(fp, "apply_finding_fix", fake_apply)

        jid = await fjm.create_job(
            db=db, user_id=u, kind="bulk", total=2,
            project_id="p1", findings=_FINDINGS_2,
        )
        for finding in _FINDINGS_2:
            run_id = fp._wallet_run_id(jid, finding)
            await wallet.assert_wallet(db, user_id=u, action="fix", run_id=run_id,
                                       idempotency_key=f"wallet_hold:{run_id}")

        await fp._run_bulk_job(
            job_id=jid, db=db, user={"user_id": u}, project_id="p1",
            findings=_FINDINGS_2, is_unlim=False,
        )
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == FIX_PRICE, (
            "f1 succeeded (settled, spent) — f2 failed (released, refunded)"
        )

    async def test_job_crash_releases_every_remaining_hold(self, db, monkeypatch):
        from routers import fix_pipeline as fp
        from services import fix_job_manager as fjm
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = await _setup_user(db, "bulkcrash", balance=2 * FIX_PRICE)

        def poisoned(_findings):
            raise RuntimeError("simulated worker crash")
        monkeypatch.setattr(fp, "_interleave_by_severity", poisoned)

        jid = await fjm.create_job(
            db=db, user_id=u, kind="bulk", total=2,
            project_id="p1", findings=_FINDINGS_2,
        )
        for finding in _FINDINGS_2:
            run_id = fp._wallet_run_id(jid, finding)
            await wallet.assert_wallet(db, user_id=u, action="fix", run_id=run_id,
                                       idempotency_key=f"wallet_hold:{run_id}")

        await fp._run_bulk_job(
            job_id=jid, db=db, user={"user_id": u}, project_id="p1",
            findings=_FINDINGS_2, is_unlim=False,
        )
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 2 * FIX_PRICE, (
            "a crash before any finding is attempted must release ALL holds"
        )

    async def test_retry_of_same_job_never_double_charges(self, db, monkeypatch):
        """Calling _run_bulk_job's settle path twice for the SAME
        finding (e.g. a duplicate background-task invocation) must
        settle only once — idempotency_key is per (job_id, finding)."""
        from routers import fix_pipeline as fp
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = await _setup_user(db, "bulkretry", balance=100)
        jid = f"fx_{uuid.uuid4().hex[:10]}"
        finding = _FINDINGS_2[0]
        run_id = fp._wallet_run_id(jid, finding)
        await wallet.assert_wallet(db, user_id=u, action="fix", run_id=run_id,
                                   idempotency_key=f"wallet_hold:{run_id}")

        await wallet.finalize_hold(db, user_id=u, action="fix", run_id=run_id, ok=True)
        await wallet.finalize_hold(db, user_id=u, action="fix", run_id=run_id, ok=True)

        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] == 100 - FIX_PRICE, "second settle call must be a no-op"

    async def test_two_concurrent_bulk_starts_never_push_balance_negative(self, db, monkeypatch):
        """Balance covers exactly one 2-finding batch, not two. The
        per-finding atomic spend pipeline (same one wallet.hold() always
        uses) guarantees the SAFETY property under real concurrency:
        the balance can never go negative and never over-spends. It
        does NOT guarantee FAIRNESS — because this design holds N
        findings as N independent sequential calls (not one atomic
        batch reservation), adversarial interleaving can have both
        callers grab one finding each and then both fail their
        second, aborting and refunding everything (net: nobody's
        batch fully completes, but nothing was ever lost or
        double-spent either). That's a real, accepted tradeoff of the
        N-independent-holds design — documented in the FULL REPORT."""
        import asyncio as _asyncio

        from routers import fix_pipeline as fp
        from services import wallet
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        u = await _setup_user(db, "bulkconcurrent", balance=2 * FIX_PRICE)

        async def fake_gate(auth, level):
            return {"user_id": u}, {"user_id": u, "tier": "team"}
        monkeypatch.setattr(fp, "_gate", fake_gate)
        monkeypatch.setattr(fp, "get_db", lambda: db)
        monkeypatch.setattr(fp.asyncio, "create_task", lambda coro: coro.close())
        from cto_services import db as _dbmod
        _dbmod.set_db(db)

        async def _try():
            try:
                await fp.start_bulk_fix(
                    body={"project_id": "p1", "findings": [
                        {"id": f"f_{uuid.uuid4().hex[:6]}", "file": "a.py", "line": 1},
                        {"id": f"f_{uuid.uuid4().hex[:6]}", "file": "b.py", "line": 2},
                    ]},
                    authorization="Bearer x",
                )
                return True
            except HTTPException:
                return False

        results = await _asyncio.gather(_try(), _try())
        n_won = sum(1 for r in results if r)
        assert n_won <= 1, "at most one 2-finding batch can fully hold against a 1-batch balance"
        bal = await wallet.get_balance(db, u)
        assert bal["spendable"] >= 0, "balance must never go negative"
        assert bal["spendable"] in (0, 2 * FIX_PRICE), (
            "either the one winner spent the whole balance, or both "
            "aborted and the balance is fully refunded — no partial leak"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
