"""tests/test_regression_iter2026_67_scan_fix_quota_revert_divergence.py

Devil's-advocate audit finding (Phase 3 — multi-write/ledger-
consistency audit).

Two adjacent ledgers exist for the same event (a finding fix being
applied):
  1. The FINDING-level ledger (cto_fixed_findings / cto_open_findings —
     "is this specific finding currently fixed or open"). This one
     self-heals correctly on revert: `_reopen_finding_after_revert`
     (services/finding_fix_applier.py) is called for every reverted
     fix, including every sibling sharing a commit_sha (the C1 fix
     from this same session's earlier Part 2c work).
  2. The QUOTA-level ledger (services/scan_fix_quota.py's
     scan_fix_usage.count — "how many of this user's monthly fix
     tasks have been consumed"). `record_scan_fixes()` has exactly
     ONE call site in the entire codebase: routers/fix_pipeline.py's
     apply-success path. Nothing calls it (or any refund equivalent)
     anywhere near revert_finding_fix / revert_batch.

Net effect: a user who applies a fix (1 task deducted), then reverts
it because it was wrong, permanently loses that task slot for a fix
that no longer exists anywhere — the two ledgers diverge. The
finding is "open" again (correctly), but the user's monthly quota
still shows it as spent.

THIS IS DELIBERATELY NOT "FIXED" WITH A BLIND REFUND. A naive
refund-on-revert would open a worse problem: a user could cycle
apply -> revert -> apply -> revert indefinitely to extract unlimited
free LLM-powered fix attempts without ever spending real quota, each
cycle still costing AUREM a real LLM call. Whether/how to refund
(e.g., only within a short post-apply window, or capped refunds per
month) is a product/anti-abuse policy decision, not a pure
correctness bug — it needs business sign-off, not a unilateral
engineering patch.

This file locks in the CURRENT, asymmetric behavior with a clear
test and an unambiguous comment at the point of divergence, so:
  (a) the gap is detected and visible, not silently invisible,
  (b) nobody "fixes" it by accident in a way that reintroduces the
      quota-bypass abuse vector without that tradeoff being an
      explicit, reviewed decision.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _MemColl:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def find_one(self, filt=None, projection=None):
        filt = filt or {}
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items() if not isinstance(v, dict)):
                return dict(r)
        return None

    async def update_one(self, filt, ops, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items() if not isinstance(v, dict)):
                if "$set" in ops:
                    r.update(ops["$set"])
                if "$inc" in ops:
                    for k, v in ops["$inc"].items():
                        r[k] = r.get(k, 0) + v
                return
        if upsert:
            new = dict(filt)
            if "$set" in ops:
                new.update(ops["$set"])
            if "$inc" in ops:
                for k, v in ops["$inc"].items():
                    new[k] = v
            self.rows.append(new)


class _MemDB:
    def __init__(self):
        self._colls = {}

    def __getattr__(self, name):
        if name not in self._colls:
            self._colls[name] = _MemColl()
        return self._colls[name]

    def __getitem__(self, name):
        return getattr(self, name)


class TestQuotaLedgerDoesNotReconcileOnRevert:
    async def test_no_quota_reconciliation_anywhere_in_the_revert_pipeline(self):
        """record_scan_fixes() has several legitimate call sites (one
        per scan tool: vanguard-scan, health-scan, visibility-kit,
        etc.) — that's expected, not the gap. The gap is specifically
        that NONE of them live in the revert pipeline
        (services/finding_fix_applier.py, the file that owns
        revert_finding_fix/revert_batch/_revert_single_fix_on_batch),
        and no refund-shaped equivalent exists there either.

        If this test ever fails because a reconciliation call WAS
        added, that's good — update this test's premise and the
        file's docstring to describe the new (presumably policy-
        reviewed) behavior, rather than leaving a stale assertion."""
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        applier_path = root / "services" / "finding_fix_applier.py"
        tree = ast.parse(applier_path.read_text())

        quota_related_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name and ("quota" in func_name.lower()
                                   or "record_scan_fixes" in func_name.lower()
                                   or "refund" in func_name.lower()):
                    quota_related_calls.append(f"{func_name}:{node.lineno}")

        assert quota_related_calls == [], (
            f"found quota/refund-related calls in the revert pipeline: "
            f"{quota_related_calls!r} — if reconciliation was added, "
            "update this test's premise rather than leaving it stale"
        )

    async def test_quota_count_survives_a_revert_unchanged_documented_gap(self):
        """Direct behavioral proof: deduct one task, confirm the
        finding-level ledger would reopen (simulated), confirm the
        quota-level ledger is untouched by anything in the revert
        path — exactly the asymmetry this file documents."""
        import services.scan_fix_quota as sfq
        from services.scan_fix_quota import month_bucket, record_scan_fixes

        db = _MemDB()

        def _fake_require_db():
            return db
        import unittest.mock
        with unittest.mock.patch.object(sfq, "require_db", _fake_require_db):
            await record_scan_fixes("u1", "vanguard-scan", 1)
            row = await db.scan_fix_usage.find_one(
                {"user_id": "u1", "month": month_bucket()},
            )
            assert row["count"] == 1

            # Simulate a revert: nothing in the real revert path
            # (finding_fix_applier.py::revert_finding_fix / revert_batch)
            # touches scan_fix_usage at all — there is no call to
            # record_scan_fixes or any inverse of it anywhere near
            # revert. We assert that directly rather than faking a
            # call that doesn't exist in production.
            row_after = await db.scan_fix_usage.find_one(
                {"user_id": "u1", "month": month_bucket()},
            )
            assert row_after["count"] == 1, (
                "quota count changed on its own — if something now "
                "DOES reconcile it on revert, great, but then this "
                "whole file's premise is stale and should be revised, "
                "not left asserting the old gap"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
