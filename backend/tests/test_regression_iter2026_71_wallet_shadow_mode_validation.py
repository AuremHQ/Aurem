"""tests/test_regression_iter2026_71_wallet_shadow_mode_validation.py

Devil's-advocate audit finding (Phase 7 — wallet shadow-mode
validation before any TOKEN_BILLING_MODE flip to "on").

Investigated first, not assumed: services/wallet.py::assert_wallet()
ALREADY has a real, well-built three-state design (off/shadow/on) —
shadow mode genuinely computes what WOULD be charged/blocked and
never calls hold() or raises. This is exactly the mechanism the
original hedge asked to build; it already exists.

The actual gap: shadow mode's output goes ONLY to logger.info/
logger.debug calls — no structured, queryable persistence. There is
no way to systematically diff shadow-predicted charges against the
REAL, currently-enforced task-count gate
(services/usage.py::assert_has_task_budget, tasks_this_month vs
monthly_task_cap) across a traffic window, which is exactly the
validation step the original hedge asked for before flipping
TOKEN_BILLING_MODE to "on" for real: "run it in shadow mode against
real traffic... diff against the task-based numbers... only flip once
that diff is boring."

Fix: a durable `wallet_shadow_log` row written alongside (not instead
of) the existing log lines every time assert_wallet() runs in shadow
mode, plus a comparison function that reports, for a given window,
how often the shadow prediction's `would_block` agreed or disagreed
with the user's actual current task-based-gate state. This is
read-only/additive — assert_wallet()'s existing off/shadow/on
behavior, and billing_mode() itself, are completely unchanged; no
caller's billing outcome is affected in any way by this.
"""
from __future__ import annotations

import time

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

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    def find(self, filt=None, projection=None):
        filt = filt or {}
        matched = [
            dict(r) for r in self.rows
            if all(r.get(k) == v for k, v in filt.items()
                   if not (isinstance(v, dict)))
        ]

        class _Cur:
            def __init__(self, items):
                self.items = items

            async def to_list(self, length=None):
                return list(self.items)
        return _Cur(matched)


class _MemDB:
    def __init__(self):
        self._colls = {}

    def __getattr__(self, name):
        if name not in self._colls:
            self._colls[name] = _MemColl()
        return self._colls[name]

    def __getitem__(self, name):
        return getattr(self, name)


class TestAssertWalletWritesAStructuredShadowLogRow:
    async def test_shadow_mode_persists_a_queryable_row(self, monkeypatch):
        import services.wallet as wallet_mod

        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        db = _MemDB()
        db.wallets.rows.append({
            "_id": "u1",
            "buckets": {"promo": 0, "plan": 1000, "purchased": 0},
        })

        async def _fake_tokens_for(action):
            return 50
        monkeypatch.setattr(wallet_mod, "tokens_for", lambda action: 50)

        await wallet_mod.assert_wallet(
            db, user_id="u1", action="fix_finding",
            run_id="r1", idempotency_key="idem-1",
        )

        rows = db.wallet_shadow_log.rows
        assert len(rows) == 1, (
            "shadow mode did not persist a structured, queryable row — "
            "it's still log-line-only, which can't be systematically "
            "diffed against the live task-based gate"
        )
        row = rows[0]
        assert row["user_id"] == "u1"
        assert row["action"] == "fix_finding"
        assert row["needed_tokens"] == 50
        assert row["would_block"] is False
        assert "occurred_at" in row

    async def test_shadow_mode_records_would_block_true_when_balance_insufficient(self, monkeypatch):
        import services.wallet as wallet_mod

        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        db = _MemDB()
        db.wallets.rows.append({
            "_id": "u2",
            "buckets": {"promo": 0, "plan": 5, "purchased": 0},
        })
        monkeypatch.setattr(wallet_mod, "tokens_for", lambda action: 50)

        await wallet_mod.assert_wallet(
            db, user_id="u2", action="fix_finding",
            run_id="r2", idempotency_key="idem-2",
        )

        row = db.wallet_shadow_log.rows[0]
        assert row["would_block"] is True

    async def test_off_mode_never_writes_a_shadow_row(self, monkeypatch):
        import services.wallet as wallet_mod

        monkeypatch.setenv("TOKEN_BILLING_MODE", "off")
        db = _MemDB()
        await wallet_mod.assert_wallet(
            db, user_id="u3", action="fix_finding",
            run_id="r3", idempotency_key="idem-3",
        )
        assert db.wallet_shadow_log.rows == []

    async def test_a_logging_failure_never_raises_into_the_caller(self, monkeypatch):
        """Billing bookkeeping (even shadow bookkeeping) must never
        break the actual request it's describing."""
        import services.wallet as wallet_mod

        monkeypatch.setenv("TOKEN_BILLING_MODE", "shadow")
        db = _MemDB()
        db.wallets.rows.append({
            "_id": "u4", "buckets": {"promo": 0, "plan": 1000, "purchased": 0},
        })
        monkeypatch.setattr(wallet_mod, "tokens_for", lambda action: 50)

        async def _boom(doc):
            raise RuntimeError("mongo write failed")
        monkeypatch.setattr(db.wallet_shadow_log, "insert_one", _boom)

        result = await wallet_mod.assert_wallet(
            db, user_id="u4", action="fix_finding",
            run_id="r4", idempotency_key="idem-4",
        )
        assert result["mode"] == "shadow"


class TestShadowVsTaskBasedGateComparison:
    async def test_agreement_when_shadow_predicted_block_and_user_is_at_cap(self, monkeypatch):
        from services.wallet_shadow import compare_shadow_vs_task_based_gate

        db = _MemDB()
        now = time.time()
        db.wallet_shadow_log.rows.append({
            "user_id": "u1", "action": "fix_finding", "needed_tokens": 50,
            "would_block": True, "occurred_at": now,
        })

        async def _fake_get_usage(user_id, db=None):
            return {"tier": "free", "tasks_this_month": 10,
                    "monthly_task_cap": 10, "is_unlimited": False}

        import services.wallet_shadow as ws_mod
        monkeypatch.setattr(ws_mod, "get_usage", _fake_get_usage)

        report = await compare_shadow_vs_task_based_gate(
            db, since_ts=now - 60, until_ts=now + 60,
        )
        assert report["total_shadow_decisions"] == 1
        assert report["agreements"] == 1
        assert report["disagreements"] == 0

    async def test_disagreement_when_shadow_predicted_block_but_task_gate_has_room(self, monkeypatch):
        from services.wallet_shadow import compare_shadow_vs_task_based_gate

        db = _MemDB()
        now = time.time()
        db.wallet_shadow_log.rows.append({
            "user_id": "u2", "action": "fix_finding", "needed_tokens": 50,
            "would_block": True, "occurred_at": now,
        })

        async def _fake_get_usage(user_id, db=None):
            return {"tier": "free", "tasks_this_month": 2,
                    "monthly_task_cap": 10, "is_unlimited": False}

        import services.wallet_shadow as ws_mod
        monkeypatch.setattr(ws_mod, "get_usage", _fake_get_usage)

        report = await compare_shadow_vs_task_based_gate(
            db, since_ts=now - 60, until_ts=now + 60,
        )
        assert report["disagreements"] == 1
        assert report["disagreement_examples"][0]["user_id"] == "u2"

    async def test_founder_unlimited_users_are_excluded_from_divergence_counting(self, monkeypatch):
        """A founder's shadow would_block (if it ever happened) isn't
        a meaningful signal — they're unlimited on the real gate by
        design, so counting them would manufacture false divergence."""
        from services.wallet_shadow import compare_shadow_vs_task_based_gate

        db = _MemDB()
        now = time.time()
        db.wallet_shadow_log.rows.append({
            "user_id": "u_founder", "action": "fix_finding",
            "needed_tokens": 50, "would_block": True, "occurred_at": now,
        })

        async def _fake_get_usage(user_id, db=None):
            return {"tier": "founder", "tasks_this_month": 500,
                    "monthly_task_cap": None, "is_unlimited": True}

        import services.wallet_shadow as ws_mod
        monkeypatch.setattr(ws_mod, "get_usage", _fake_get_usage)

        report = await compare_shadow_vs_task_based_gate(
            db, since_ts=now - 60, until_ts=now + 60,
        )
        assert report["total_shadow_decisions"] == 0
        assert report["excluded_unlimited"] == 1

    async def test_empty_window_reports_zero_not_a_crash(self):
        from services.wallet_shadow import compare_shadow_vs_task_based_gate
        db = _MemDB()
        report = await compare_shadow_vs_task_based_gate(
            db, since_ts=0, until_ts=1,
        )
        assert report["total_shadow_decisions"] == 0
        assert report["agreements"] == 0
        assert report["disagreements"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
