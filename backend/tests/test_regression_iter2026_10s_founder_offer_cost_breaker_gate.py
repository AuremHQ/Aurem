"""tests/test_regression_iter2026_10s_founder_offer_cost_breaker_gate.py

Closing-the-bypass-doors audit — Step 4.

`routers/founder_offer.py` (the "free SEO fix, 500 spots" promotional
giveaway) calls `services/seo/orchestrator.py::run_seo_fixes`, which
generates real LLM alt-text via `services.llm.call_llm` — a shortcut
that (per its own docstring) SKIPS `call_llm_with_meta`'s
`assert_within_cap` platform-wide cost breaker entirely. Confirmed by
reading `services/llm/__init__.py::call_llm`'s docstring directly, not
assumed from the audit's one-line note.

Deliberately NOT gated behind payment/fix_entitlement — this feature
is explicitly free (the whole point), and locking it behind payment
would contradict the product. Instead both call sites
(`claim_offer`'s dry-run preview and `_run_real_fix`'s real commit)
now call the SAME platform-wide `assert_within_cap` breaker every
other LLM path already relies on for infra-abuse protection.

Stubs every other dependency (singleton doc, user-claims lookup,
notifications) to isolate exactly the new gate's behavior — same
"stub the rest, verify only the gate" approach as
test_iter212m130_loop_founder_gate.py.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


class _FakeFounderOfferColl:
    async def find_one_and_update(self, query, update, **kw):
        return {"_id": "global", "total_spots": 500, "spots_claimed": 1,
                "is_active": True}

    async def update_one(self, query, update):
        class _R:
            matched_count = 1
        return _R()


class _FakeUserSeoClaimsColl:
    def __init__(self):
        self.rows = []

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "fake"
        return _R()

    async def update_one(self, query, update):
        for r in self.rows:
            if r.get("claim_id") == query.get("claim_id"):
                r.update(update.get("$set") or {})
        class _R:
            matched_count = 1
        return _R()

    def find(self, query=None, **kw):
        class _Cur:
            async def to_list(self, length=None):
                return []
        return _Cur()


class _FakeDB:
    def __init__(self):
        self.founder_offer = _FakeFounderOfferColl()
        self.user_seo_claims = _FakeUserSeoClaimsColl()


async def _call_claim_offer(monkeypatch, *, cap_tripped: bool):
    from routers import founder_offer as fo

    async def fake_current_dev(auth=None):
        return {"user_id": "u1", "email": "u1@aurem.dev"}
    monkeypatch.setattr(fo, "current_dev", fake_current_dev)

    fake_db = _FakeDB()
    monkeypatch.setattr(fo, "get_db", lambda: fake_db)
    monkeypatch.setattr(fo, "_reap_stale_previews", AsyncMock(return_value=None))
    monkeypatch.setattr(fo, "_user_claims", AsyncMock(return_value=[]))
    monkeypatch.setattr(fo, "_ensure_singleton", AsyncMock(return_value={
        "_id": "global", "total_spots": 500, "spots_claimed": 1, "is_active": True,
    }))

    async def fake_emit_notification(*a, **kw):
        return None
    monkeypatch.setattr(
        "services.notifications.emit_notification", fake_emit_notification,
    )

    async def fake_assert_within_cap(db, **kw):
        if cap_tripped:
            raise HTTPException(429, {"error": "llm_cost_cap_hit",
                                       "error_code": "COST_CAP_HOURLY"})
    monkeypatch.setattr(
        "services.llm_cost_breaker.assert_within_cap", fake_assert_within_cap,
    )

    ran_seo_fix = {"called": False}
    async def fake_run_seo_fixes(**kw):
        ran_seo_fix["called"] = True
        return {"ok": True, "patches": []}
    monkeypatch.setattr(
        "services.seo.orchestrator.run_seo_fixes", fake_run_seo_fixes,
    )

    body = fo._ClaimBody(repo_id="p1", site_url="https://example.com")
    return fo, fake_db, ran_seo_fix, body


class TestFounderOfferCostBreakerGate:
    async def test_cap_tripped_blocks_before_any_llm_call(self, monkeypatch):
        fo, fake_db, ran_seo_fix, body = await _call_claim_offer(
            monkeypatch, cap_tripped=True,
        )
        with pytest.raises(HTTPException) as exc_info:
            await fo.claim_offer(body, authorization="Bearer x")
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail["error_code"] == "COST_CAP_HOURLY"
        assert ran_seo_fix["called"] is False, (
            "the cap must block BEFORE run_seo_fixes — no LLM spend "
            "once the platform-wide breaker has tripped"
        )
        # The spot is still reserved (that part of /claim already
        # succeeded) — this gate only stops the LLM spend, not the
        # free-giveaway bookkeeping around it.
        assert len(fake_db.user_seo_claims.rows) == 1
        assert fake_db.user_seo_claims.rows[0]["fix_status"] == "preview"

    async def test_cap_clear_runs_the_real_seo_fix_as_before(self, monkeypatch):
        fo, fake_db, ran_seo_fix, body = await _call_claim_offer(
            monkeypatch, cap_tripped=False,
        )
        result = await fo.claim_offer(body, authorization="Bearer x")
        assert result["success"] is True
        assert ran_seo_fix["called"] is True
        assert len(fake_db.user_seo_claims.rows) == 1


class TestRealFixRunnerCostBreakerGate:
    async def test_cap_tripped_marks_claim_failed_not_a_raw_exception(self, monkeypatch):
        from routers import founder_offer as fo
        fake_db = _FakeDB()
        fake_db.user_seo_claims.rows.append({
            "claim_id": "c1", "user_id": "u1", "fix_status": "running",
            "created_at": time.time(),
        })
        monkeypatch.setattr(fo, "get_db", lambda: fake_db)

        async def fake_assert_within_cap(db, **kw):
            raise HTTPException(429, {"error": "llm_cost_cap_hit"})
        monkeypatch.setattr(
            "services.llm_cost_breaker.assert_within_cap", fake_assert_within_cap,
        )
        ran_seo_fix = {"called": False}
        async def fake_run_seo_fixes(**kw):
            ran_seo_fix["called"] = True
            return {"ok": True}
        monkeypatch.setattr(
            "services.seo.orchestrator.run_seo_fixes", fake_run_seo_fixes,
        )

        # Must not raise — it's a fire-and-forget background runner.
        await fo._run_real_fix(
            claim_id="c1", user_id="u1", repo_id="p1", site_url="https://x.com",
        )
        assert ran_seo_fix["called"] is False
        assert fake_db.user_seo_claims.rows[0]["fix_status"] == "failed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
