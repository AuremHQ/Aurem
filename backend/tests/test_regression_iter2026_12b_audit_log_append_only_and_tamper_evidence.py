"""tests/test_regression_iter2026_12b_audit_log_append_only_and_tamper_evidence.py

Master Prompt v2 continuation — PRE-STEP (b) (fixing a PART 3 review
finding before PART 4 starts):

The PART 3 turn's `test_audit_row_is_written_and_never_mutated` only
proved append-only by `inspect.getsource`-grepping pricing_engine.py
for the ABSENCE of `.update`/`.delete`/`.find_one_and_update` calls on
`pricing_audit_log`. That is a source-inspection test, not a
behavioral one: it proves nothing about whether an admin API endpoint
(or any future module function) could mutate a row, and it proves
nothing about a row being silently deleted or edited directly in
Mongo (e.g. by anyone with direct DB access, or a bug in an unrelated
module).

This file replaces it with BEHAVIORAL tests:
  1. Every function CURRENTLY exported by the audit-log section of
     services/pricing_engine.py (write_audit_row, list_audit_log) is
     exercised and shown to be strictly append/read-only — writing
     twice never overwrites, and there is no mutating entry point.
  2. Every admin endpoint in routers/admin_pricing.py that reaches the
     audit log is enumerated — there is no PUT/PATCH/DELETE route for
     `pricing_audit_log` at all, which this test asserts against the
     real FastAPI route table (so a future route addition fails this
     test immediately rather than silently reopening the hole).
  3. A direct Mongo update_one/delete_one/replace_one against the
     `pricing_audit_log` collection is attempted (this is expected to
     SUCCEED at the driver level — Mongo itself has no row-level ACL;
     "cannot succeed via the app" means no app-level code path reaches
     such a call, which (1)+(2) establish). This test exists to prove
     the NEW tamper-evidence chain (added below) actually DETECTS such
     a direct-Mongo tamper attempt, since the driver-level block the
     PART 3 test imagined never existed.
  4. A new hash-chain tamper-evidence mechanism: every row stores
     `prev_hash` (the previous row's `row_hash`) and `row_hash` (a
     sha256 of `prev_hash` + this row's own content, computed via
     services.pricing_engine._audit_row_hash). A new
     `verify_audit_chain(db)` walks the chain in write order and
     reports the first row whose stored hash no longer matches what
     its content + predecessor would produce. Both an EDITED row and
     a DELETED row are proven detectable.

Concurrency note: write_audit_row's chain-head pointer
(`pricing_audit_chain_head`) is updated via an optimistic
compare-and-swap retry loop (same pattern as the existing FIFO batch
version CAS in services/wallet.py) specifically so concurrent writers
can never both read the same "previous hash" and fork the chain. A
concurrent-writers test below proves the chain stays linear (strictly
increasing version, zero forks) under a burst of parallel admin
actions, which is exactly the shape PART 3's existing
concurrent-margin-save test already exercises for the pricing_versions
collection.

Per HARD RULES v2 T1 (TDD): this file is written and run RED before
_audit_row_hash/verify_audit_chain exist in services/pricing_engine.py.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.asyncio

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
_live_available = bool(MONGO_URL and DB_NAME)


def _actor(tag: str) -> str:
    return f"wallet_test_{tag}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def db():
    if not _live_available:
        pytest.skip("MONGO_URL/DB_NAME not set")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    database = client[DB_NAME]
    from cto_services import db as _dbmod
    _dbmod.set_db(database)
    yield database
    _dbmod.set_db(None)
    client.close()


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    if _live_available:
        await db.pricing_audit_log.delete_many({"actor": {"$regex": "^wallet_test_"}})
        await db.pricing_audit_chain_head.delete_many({})


class TestNoMutatingEntryPointExists:
    async def test_write_audit_row_never_overwrites_a_previous_row(self, db):
        from services.pricing_engine import list_audit_log, write_audit_row
        actor = _actor("audA")
        row1 = await write_audit_row(db, action="margin_change", actor=actor,
                                      before={"margin": 0.3}, after={"margin": 0.5})
        row2 = await write_audit_row(db, action="margin_change", actor=actor,
                                      before={"margin": 0.5}, after={"margin": 0.6})
        assert row1["_id"] != row2["_id"]
        rows = await list_audit_log(db)
        ids = {r["_id"] for r in rows}
        assert row1["_id"] in ids and row2["_id"] in ids
        found1 = next(r for r in rows if r["_id"] == row1["_id"])
        # row1 must still show its ORIGINAL content, unaffected by row2.
        assert found1["before"] == {"margin": 0.3}
        assert found1["after"] == {"margin": 0.5}

    async def test_pricing_engine_module_exposes_no_update_or_delete_audit_function(self):
        import services.pricing_engine as pe
        names = [n for n in dir(pe) if "audit" in n.lower()]
        for n in names:
            assert not any(n.lower().startswith(p) for p in ("update_", "delete_", "edit_", "remove_", "replace_")), (
                f"found a mutating-looking audit function: {n}"
            )

    async def test_no_admin_route_can_mutate_or_delete_an_audit_row(self):
        """Behavioral proof at the FastAPI route-table level: there is
        no PUT/PATCH/DELETE route anywhere under /admin/pricing/audit
        or /admin/pricing/audit-log. A future dev adding one breaks
        this test immediately."""
        from routers.admin_pricing import router
        for route in router.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if "audit" in path:
                assert methods.issubset({"GET", "HEAD", "OPTIONS"}), (
                    f"audit route {path} exposes mutating methods: {methods}"
                )


class TestDirectMongoTamperIsDetectedByChain:
    async def test_editing_a_row_directly_in_mongo_breaks_chain_verification(self, db):
        from services.pricing_engine import verify_audit_chain, write_audit_row
        actor = _actor("audB")
        await write_audit_row(db, action="margin_change", actor=actor,
                               before={"margin": 0.1}, after={"margin": 0.2})
        row2 = await write_audit_row(db, action="margin_change", actor=actor,
                                      before={"margin": 0.2}, after={"margin": 0.3})

        result_before = await verify_audit_chain(db)
        assert result_before["ok"] is True

        # Attempt the tamper directly against the Mongo collection
        # (bypassing every app function) — this driver-level call is
        # expected to succeed; what matters is that it's detectable.
        await db.pricing_audit_log.update_one(
            {"_id": row2["_id"]}, {"$set": {"after": {"margin": 99.0}}},
        )
        result_after = await verify_audit_chain(db)
        assert result_after["ok"] is False
        assert result_after["broken_at"] == row2["_id"]

    async def test_deleting_a_row_directly_in_mongo_breaks_chain_verification(self, db):
        from services.pricing_engine import verify_audit_chain, write_audit_row
        actor = _actor("audC")
        row1 = await write_audit_row(db, action="fixed_cost_upsert", actor=actor,
                                      before=None, after={"name": "hosting", "monthly_usd": 500})
        row2 = await write_audit_row(db, action="fixed_cost_upsert", actor=actor,
                                      before={"name": "hosting", "monthly_usd": 500},
                                      after={"name": "hosting", "monthly_usd": 600})

        assert (await verify_audit_chain(db))["ok"] is True

        # Delete the FIRST row directly via Mongo — row2's stored
        # prev_hash now points at a hash no longer reachable from the
        # (now-shorter) chain, so verification must catch it.
        await db.pricing_audit_log.delete_one({"_id": row1["_id"]})
        result = await verify_audit_chain(db)
        assert result["ok"] is False
        assert result["broken_at"] == row2["_id"]

    async def test_replacing_a_row_directly_in_mongo_breaks_chain_verification(self, db):
        from services.pricing_engine import verify_audit_chain, write_audit_row
        actor = _actor("audD")
        row1 = await write_audit_row(db, action="margin_change", actor=actor,
                                      before={"margin": 0.4}, after={"margin": 0.45})

        await db.pricing_audit_log.replace_one(
            {"_id": row1["_id"]},
            {"_id": row1["_id"], "action": "margin_change", "actor": actor,
             "before": {"margin": 0.4}, "after": {"margin": 0.45},
             "created_at": row1["created_at"], "prev_hash": row1["prev_hash"],
             "row_hash": "forged" * 10},
        )
        result = await verify_audit_chain(db)
        assert result["ok"] is False
        assert result["broken_at"] == row1["_id"]

    async def test_untampered_chain_of_many_rows_verifies_ok(self, db):
        from services.pricing_engine import verify_audit_chain, write_audit_row
        actor = _actor("audE")
        for i in range(25):
            await write_audit_row(db, action="margin_change", actor=actor,
                                   before={"margin": i / 100}, after={"margin": (i + 1) / 100})
        result = await verify_audit_chain(db)
        assert result["ok"] is True
        assert result["checked"] >= 25

    async def test_empty_log_verifies_ok(self, db):
        from services.pricing_engine import verify_audit_chain
        result = await verify_audit_chain(db)
        assert result["ok"] is True
        assert result["broken_at"] is None


class TestHashChainStructure:
    async def test_first_row_chains_onto_genesis(self, db):
        from services.pricing_engine import write_audit_row
        actor = _actor("audF")
        row = await write_audit_row(db, action="margin_change", actor=actor,
                                     before=None, after={"margin": 0.2})
        assert row["prev_hash"] == "genesis"
        assert isinstance(row["row_hash"], str) and len(row["row_hash"]) == 64

    async def test_second_row_chains_onto_first_rows_hash(self, db):
        from services.pricing_engine import write_audit_row
        actor = _actor("audG")
        row1 = await write_audit_row(db, action="margin_change", actor=actor,
                                      before=None, after={"margin": 0.2})
        row2 = await write_audit_row(db, action="margin_change", actor=actor,
                                      before={"margin": 0.2}, after={"margin": 0.3})
        assert row2["prev_hash"] == row1["row_hash"]

    async def test_two_rows_with_identical_before_after_still_get_distinct_hashes(self, db):
        """Two rows with the SAME content (different only by _id/
        created_at/chain position) must not collide — proves the hash
        input includes more than just before/after."""
        from services.pricing_engine import write_audit_row
        actor = _actor("audH")
        row1 = await write_audit_row(db, action="margin_change", actor=actor,
                                      before={"margin": 0.5}, after={"margin": 0.5})
        row2 = await write_audit_row(db, action="margin_change", actor=actor,
                                      before={"margin": 0.5}, after={"margin": 0.5})
        assert row1["row_hash"] != row2["row_hash"]


class TestConcurrentWritesStayLinear:
    async def test_burst_of_concurrent_audit_writes_forms_one_unbroken_chain(self, db):
        """Mirrors PART 3's concurrent-margin-save shape: many admin
        actions landing audit rows at the same instant must not fork
        the hash chain (two rows both claiming the same prev_hash)."""
        from services.pricing_engine import verify_audit_chain, write_audit_row
        actor = _actor("audI")

        async def _one(i):
            return await write_audit_row(
                db, action="margin_change", actor=actor,
                before={"margin": i / 1000}, after={"margin": (i + 1) / 1000},
            )

        rows = await asyncio.gather(*[_one(i) for i in range(30)])
        prev_hashes = [r["prev_hash"] for r in rows]
        row_hashes = [r["row_hash"] for r in rows]
        # No two concurrent writers were allowed to claim the same
        # predecessor hash (that would mean a fork).
        assert len(set(prev_hashes)) == len(prev_hashes)
        assert len(set(row_hashes)) == len(row_hashes)

        result = await verify_audit_chain(db)
        assert result["ok"] is True
        assert result["checked"] >= 30


class TestVerifyAuditChainIsExposedToAdmin:
    async def test_audit_log_endpoint_or_a_sibling_can_report_chain_integrity(self):
        """verify_audit_chain must be reachable from the admin surface
        (not just importable in a test) so a real tamper would
        actually surface to an admin, not only to this test file."""
        import inspect

        import routers.admin_pricing as mod
        src = inspect.getsource(mod)
        assert "verify_audit_chain" in src, (
            "routers/admin_pricing.py never calls verify_audit_chain — "
            "the admin audit-log endpoint should surface chain integrity"
        )
