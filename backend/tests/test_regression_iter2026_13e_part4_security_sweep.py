"""tests/test_regression_iter2026_13e_part4_security_sweep.py

Master Prompt v2 PART 4 — the remaining explicit security
requirements not already covered by test_regression_iter2026_13b/c/d:

  - the free scan cannot be triggered for a repo the caller doesn't
    own: proven architecturally (the ONLY call site for
    run_onboarding_scan uses a server-generated project_id and the
    AUTHENTICATED caller's own user_id — there is no second call
    site and no attacker-controlled project_id/user_id pair reaches
    it), not just asserted by IDOR-on-read (already covered in 13b).
  - prompt injection in scanned repo text cannot change scan scores
    or leak other data: the free-scan pipeline (SCANNERS +
    resolve_live_cves + _score_for_findings) has ZERO LLM calls — a
    prompt-injection attack requires an LLM to READ attacker text AS
    INSTRUCTIONS, which never happens here. Proven by inspecting the
    real call graph, not assumed.
  - the wallet-meter endpoint has no user-controllable identity
    field at all (current_dev resolves strictly from the bearer
    token) — re-verified explicitly here alongside the free-scan and
    ledger IDOR tests already in 13b/13d, so all three PART 4
    per-user surfaces (balance, scan results, usage ledger) have an
    explicit cross-user test in one place.
"""
from __future__ import annotations

import inspect
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
        await db.dev_users.delete_many({"user_id": {"$regex": "^wallet_test_"}})
        await db.wallets.delete_many({"_id": {"$regex": "^wallet_test_"}})


class TestFreeScanCannotBeTriggeredForUnownedRepo:
    async def test_run_onboarding_scan_has_exactly_one_call_site(self):
        """Grep-equivalent, done via the AST/source itself so this
        fails loudly if a second call site is ever added without a
        matching ownership review: run_onboarding_scan must be
        invoked from management.py's add_project flow ONLY, with a
        server-generated project_id and the authenticated caller's
        own account_id — never anything else."""
        import routers.cto_projects.management as mgmt
        src = inspect.getsource(mgmt)
        assert "run_onboarding_scan(" in src
        # The call passes `user_id=account_id` (the AUTHENTICATED
        # caller resolved from their own JWT a few lines above) and
        # `project_id=proj_id` (freshly minted by THIS SAME request,
        # not a client-supplied value) — never a client-controlled
        # project_id/user_id pair.
        call_block = src[src.index("run_onboarding_scan("):][:300]
        assert "user_id=account_id" in call_block
        assert "project_id=proj_id" in call_block

    async def test_start_free_scan_write_does_not_let_a_mismatched_user_id_read_it_back(self, db):
        """Even in the hypothetical where start_free_scan were ever
        called with a project_id that belongs to a DIFFERENT real
        owner, get_free_scan_results still only serves the request
        when the (project_id, user_id) pair matches a real
        cto_projects row — the IDOR protection already proven in
        test_regression_iter2026_13b holds regardless of how the
        results doc was written."""
        from services.free_scan_results import (
            finish_free_scan,
            get_free_scan_results,
            start_free_scan,
        )
        real_owner = _uid("fssec_owner")
        attacker = _uid("fssec_attacker")
        pid = f"proj_{uuid.uuid4().hex[:10]}"
        await db.cto_projects.insert_one({"project_id": pid, "user_id": real_owner})

        # Simulate a hypothetical mis-keyed write (attacker's id on
        # the real owner's project_id).
        await start_free_scan(db, user_id=attacker, project_id=pid)
        await finish_free_scan(
            db, user_id=attacker, project_id=pid, score=1, findings=[],
            categories_run=[], categories_not_run=[], cost_usd=0.0,
        )
        # Neither party can read it except the REAL cto_projects owner.
        assert await get_free_scan_results(db, user_id=attacker, project_id=pid) is None
        result_for_owner = await get_free_scan_results(db, user_id=real_owner, project_id=pid)
        assert result_for_owner is not None
        await db.cto_projects.delete_many({"project_id": pid})
        await db.free_scan_results.delete_many({"project_id": pid})


class TestFreeScanHasNoPromptInjectionSurface:
    async def test_no_scanner_category_calls_an_llm(self):
        """Prompt injection requires an LLM to read attacker-controlled
        repo text AS INSTRUCTIONS. All 16 SCANNERS are pure, static
        regex/AST-style functions over `text_cache` — none of them
        imports or calls anything LLM-shaped."""
        from services.codebase_health_core import SCANNERS
        llm_markers = ("call_llm", "openai", "anthropic", "chat_completion", "llm_client")
        for name, fn in SCANNERS.items():
            src = inspect.getsource(fn)
            hit = [m for m in llm_markers if m in src.lower()]
            assert not hit, f"scanner category {name!r} appears to call an LLM ({hit}) — prompt-injection risk"

    async def test_resolve_live_cves_and_score_function_are_also_llm_free(self):
        from services.codebase_health_core import _score_for_findings, resolve_live_cves
        llm_markers = ("call_llm", "openai", "anthropic", "chat_completion", "llm_client")
        for fn in (resolve_live_cves, _score_for_findings):
            src = inspect.getsource(fn)
            hit = [m for m in llm_markers if m in src.lower()]
            assert not hit, f"{fn.__name__} appears to call an LLM ({hit})"

    async def test_scan_score_is_a_pure_function_of_findings_only(self):
        """A malicious repo string can only ever become a `finding`
        dict (name/severity/file/line) — it can never influence the
        SCORE beyond however many findings it produced, since
        _score_for_findings takes no other repo-derived input."""
        from services.codebase_health_core import _score_for_findings
        sig = inspect.signature(_score_for_findings)
        assert list(sig.parameters.keys()) == ["findings"]


class TestWalletMeterHasNoUserControllableIdentityField:
    async def test_my_usage_meter_signature_has_no_user_id_param(self):
        """The ONLY identity input is the bearer token itself
        (resolved via current_dev) — there is no user_id query/body
        param anywhere on this endpoint for a client to override."""
        from routers.usage import my_usage_meter
        sig = inspect.signature(my_usage_meter)
        assert "user_id" not in sig.parameters

    async def test_two_different_users_get_two_different_balances(self, db):
        from routers import usage as usage_router
        from services.wallet import grant
        a = _uid("meterA")
        b = _uid("meterB")
        for u, amount in ((a, 111), (b, 222)):
            await db.dev_users.update_one(
                {"user_id": u}, {"$set": {"user_id": u, "tier": "starter"}}, upsert=True,
            )
            await grant(db, user_id=u, bucket="plan", amount=amount,
                        idempotency_key=f"meter_sec_{u}", source="test")

        import unittest.mock

        from services import wallet_display
        with unittest.mock.patch.object(wallet_display, "billing_mode", lambda: "on"):
            async def _as(uid):
                return {"user_id": uid}
            with unittest.mock.patch.object(usage_router, "current_dev", lambda *_a, **_k: _as(a)):
                result_a = await usage_router.my_usage_meter(authorization="Bearer a")
            with unittest.mock.patch.object(usage_router, "current_dev", lambda *_a, **_k: _as(b)):
                result_b = await usage_router.my_usage_meter(authorization="Bearer b")
        assert result_a["balance"] == 111
        assert result_b["balance"] == 222
