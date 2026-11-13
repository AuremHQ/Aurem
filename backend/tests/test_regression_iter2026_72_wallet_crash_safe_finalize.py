"""tests/test_regression_iter2026_72_wallet_crash_safe_finalize.py

Security audit finding (P0, financial integrity): `_claim_hold()`
atomically removed (`$unset`) a hold to prevent the double-refund race
(see test_regression_iter2026_11c's TestConcurrentSettleReleaseRace),
but a process crash between that removal and the subsequent bucket-
credit + `wallet_ledger` write in settle()/release() used to lose the
held tokens with NO trace anywhere — a retry of the same
idempotency_key would find no ledger row, re-claim, get `None` back
(hold already gone), and silently report "nothing to release" instead
of recovering the lost amount.

Fixed: `_claim_hold()` now durably copies the claimed hold's full
contents into `wallets.pending_finalizations.{idempotency_key}` in the
SAME atomic operation that removes it from `holds`, so a crash right
after still leaves full recovery information behind.
`reconcile_stale_holds()` finds any such entry older than
`grace_seconds` with no matching ledger row and completes it (full
refund).

These tests simulate "the process crashed right after claiming the
hold" by calling the real `_claim_hold()` directly and deliberately
NOT calling the rest of settle()/release() afterward — this is exactly
what a real crash leaves behind (a claimed-but-unfinished hold), with
no need to actually kill a process mid-await to prove the recovery
path works. Runs against the real local MongoDB (same convention as
test_regression_iter2026_10h / 11c).
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)


def _uid(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def db():
    if not _live_available:
        pytest.skip("MONGO_URL/DB_NAME not set — wallet tests need a real Mongo")
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


class TestClaimSurvivesAsPendingFinalization:
    async def test_claim_hold_writes_pending_finalization_entry(self, db):
        from services import wallet
        u = _uid("pf_write")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g_{u}", source="test")
        await wallet.hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                           idempotency_key=f"h_{u}")  # held = 12

        # Simulate "crashed right after claiming" — call the claim
        # itself and stop, exactly what a real process death leaves.
        claimed = await wallet._claim_hold(
            db, u, f"run_{u}", idempotency_key=f"release_{u}", claim_kind="release",
        )
        assert claimed is not None
        assert claimed["amount"] == 12

        w = await db.wallets.find_one({"_id": u})
        assert f"run_{u}" not in (w.get("holds") or {}), "hold must be removed from holds"
        pf = (w.get("pending_finalizations") or {}).get(f"release_{u}")
        assert pf is not None, "claimed hold must survive in pending_finalizations"
        assert pf["amount"] == 12
        assert pf["claim_kind"] == "release"
        assert pf["run_id"] == f"run_{u}"
        # No ledger row yet — the "crash" happened before that write.
        assert await db.wallet_ledger.find_one({"_id": f"release_{u}"}) is None


class TestReconcileRecoversInterruptedRelease:
    async def test_crashed_release_is_recovered_in_full(self, db):
        from services import wallet
        u = _uid("recover_release")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g_{u}", source="test")
        await wallet.hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                           idempotency_key=f"h_{u}")  # held = 12
        bal_after_hold = await wallet.get_balance(db, u)
        assert bal_after_hold["purchased"] == 88

        # Simulate the crash: claim, then nothing else (no bucket
        # credit, no ledger write — exactly what release() normally
        # does next, but never gets to run).
        await wallet._claim_hold(
            db, u, f"run_{u}", idempotency_key=f"release_{u}", claim_kind="release",
        )
        bal_mid_crash = await wallet.get_balance(db, u)
        assert bal_mid_crash["purchased"] == 88, "tokens correctly still held, not yet refunded"

        res = await wallet.reconcile_stale_holds(db, grace_seconds=0)
        matches = [r for r in res["resolved"] if r["idempotency_key"] == f"release_{u}"]
        assert len(matches) == 1
        assert matches[0]["amount"] == 12

        bal_after_reconcile = await wallet.get_balance(db, u)
        assert bal_after_reconcile["purchased"] == 100, "the 12 held tokens must be fully recovered"
        ledger_row = await db.wallet_ledger.find_one({"_id": f"release_{u}"})
        assert ledger_row is not None and ledger_row["amount"] == 12
        w = await db.wallets.find_one({"_id": u})
        assert f"release_{u}" not in (w.get("pending_finalizations") or {}), \
            "pending_finalizations entry must be cleared after recovery"

    async def test_recent_claim_is_left_alone_not_prematurely_reconciled(self, db):
        """A claim that happened a moment ago might just be a normal
        in-flight settle()/release() about to finish on its own — the
        grace window exists specifically so the reconciler doesn't
        race a caller that's still actually running."""
        from services import wallet
        u = _uid("too_recent")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g_{u}", source="test")
        await wallet.hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                           idempotency_key=f"h_{u}")
        await wallet._claim_hold(
            db, u, f"run_{u}", idempotency_key=f"release_{u}", claim_kind="release",
        )
        res = await wallet.reconcile_stale_holds(db, grace_seconds=300)
        matches = [r for r in res["resolved"] if r["idempotency_key"] == f"release_{u}"]
        assert matches == [], "a claim younger than the grace window must not be touched"
        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 88, "balance must be untouched while still within the grace window"


class TestReconcileRecoversInterruptedSettle:
    async def test_crashed_settle_is_recovered_as_a_full_refund(self, db):
        """A settle() that crashed before writing its ledger row never
        recorded what the real charge should have been — the only
        safe recovery is a full refund of the held amount (the system
        already treats "never charge more than the hold" as the
        universal ceiling; refunding it all is the always-safe edge of
        that same rule)."""
        from services import wallet
        u = _uid("recover_settle")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g_{u}", source="test")
        await wallet.hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                           idempotency_key=f"h_{u}")  # held = 12
        await wallet._claim_hold(
            db, u, f"run_{u}", idempotency_key=f"settle_{u}", claim_kind="settle",
        )
        res = await wallet.reconcile_stale_holds(db, grace_seconds=0)
        matches = [r for r in res["resolved"] if r["idempotency_key"] == f"settle_{u}"]
        assert len(matches) == 1 and matches[0]["amount"] == 12

        bal = await wallet.get_balance(db, u)
        assert bal["purchased"] == 100
        ledger_row = await db.wallet_ledger.find_one({"_id": f"settle_{u}"})
        assert ledger_row is not None and ledger_row["amount"] == 12


class TestNormalPathStillCleansUpPendingFinalizations:
    async def test_successful_release_leaves_no_pending_finalization_behind(self, db):
        from services import wallet
        u = _uid("clean_release")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g_{u}", source="test")
        await wallet.hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                           idempotency_key=f"h_{u}")
        await wallet.release(db, user_id=u, run_id=f"run_{u}", idempotency_key=f"rel_{u}")
        w = await db.wallets.find_one({"_id": u})
        assert not (w.get("pending_finalizations") or {}), \
            "a normal, uninterrupted release() must not leave bookkeeping behind"

    async def test_successful_settle_leaves_no_pending_finalization_behind(self, db):
        from services import wallet
        u = _uid("clean_settle")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g_{u}", source="test")
        await wallet.hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                           idempotency_key=f"h_{u}")
        await wallet.settle(db, user_id=u, run_id=f"run_{u}", actual_tokens=5,
                             idempotency_key=f"settle_{u}")
        w = await db.wallets.find_one({"_id": u})
        assert not (w.get("pending_finalizations") or {}), \
            "a normal, uninterrupted settle() must not leave bookkeeping behind"

    async def test_reconciler_does_not_double_refund_a_completed_operation(self, db):
        """If the ledger row already exists (the real caller finished
        just fine), the reconciler must only clean up, never credit
        tokens a second time."""
        from services import wallet
        u = _uid("no_double")
        await wallet.grant(db, user_id=u, bucket="purchased", amount=100,
                            idempotency_key=f"g_{u}", source="test")
        await wallet.hold(db, user_id=u, action="fix", run_id=f"run_{u}",
                           idempotency_key=f"h_{u}")
        await wallet.release(db, user_id=u, run_id=f"run_{u}", idempotency_key=f"rel_{u}")
        bal_once = await wallet.get_balance(db, u)
        assert bal_once["purchased"] == 100

        # Even if a stray pending_finalizations entry existed (it
        # shouldn't, per the test above, but defend the invariant
        # directly): running the reconciler again must be a no-op.
        res = await wallet.reconcile_stale_holds(db, grace_seconds=0)
        assert res["resolved"] == []
        bal_twice = await wallet.get_balance(db, u)
        assert bal_twice["purchased"] == 100, "must never double-refund"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
