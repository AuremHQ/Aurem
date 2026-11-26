"""
tests/test_audit2_prompt1_leak_closes_2026_11_26.py

Audit-2 Prompt 1 (token-leak closes), verified in
~/workspace/tmp/ora-audit-verify-2026-11-26/REPORT.md §2/§5 —
all six items below were CONFIRMED against live main ece2416f
before this fix was written. Each section pins one leak closed:

  #1  intent classifier bills to "anonymous" (no user_id threaded)
  #2  Ask Advisor (ora_panel) skips the only live task gate
  #3  quota check fails OPEN on infra errors
  #4  resolve_loop_mode defaults account_has_pro=True (fail-open)
  #5  per-user $6.50/month cap has zero call sites repo-wide
  #6  fix engine hardcodes review_mode="pro" for every plan

Explicitly NOT in this file (separate work, do not add here):
  - TOKEN_BILLING_MODE flip / shadow / backfill (Prompt 4)
  - pricing copy or pricing numbers
  - in-chat checkout on 402 (Prompt 2)
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

# ══════════════════════════════════════════════════════════════════
# #1 — intent classifier user_id threading
# ══════════════════════════════════════════════════════════════════
from core import intent_gateway as ig
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_classify_llm_passes_user_id_to_call_llm(monkeypatch):
    """Leak #1 — the classifier's LLM spend must be attributed to the
    real user, not the ledger's "anonymous" fallback."""
    from services import llm as _llm_mod

    seen = {}

    async def _fake_call_llm(messages, system="", max_tokens=4000,
                             temperature=0.7, user_id=None, **kw):
        seen["user_id"] = user_id
        return '{"tier":"agentic","conf":0.9}'

    monkeypatch.setattr(_llm_mod, "call_llm", _fake_call_llm)
    r = await ig._classify_llm("fix the auth bug", [], user_id="u1")
    assert seen["user_id"] == "u1"
    assert r["tier"] == ig.TIER_AGENTIC
    assert r["method"] == "llm"


@pytest.mark.asyncio
async def test_classify_llm_default_user_id_is_none(monkeypatch):
    """No user_id passed → call_llm still gets user_id=None (old
    behaviour preserved for callers with no user)."""
    from services import llm as _llm_mod

    seen = {}

    async def _fake_call_llm(messages, system="", max_tokens=4000,
                             temperature=0.7, user_id=None, **kw):
        seen["user_id"] = user_id
        return '{"tier":"query","conf":0.8}'

    monkeypatch.setattr(_llm_mod, "call_llm", _fake_call_llm)
    r = await ig._classify_llm("what is my pipeline status", [])
    assert seen["user_id"] is None
    assert r["tier"] == ig.TIER_QUERY


@pytest.mark.asyncio
async def test_classify_forwards_its_user_id_to_llm_fallback(monkeypatch):
    """classify() must pass its own user_id into _classify_llm —
    the exact gap the audit found (L549/L580 called it bare)."""
    seen = {}

    async def _stub_llm(message, history, user_id=None, **kw):
        seen["user_id"] = user_id
        return {"tier": ig.TIER_QUERY, "confidence": 0.80,
                "method": "llm", "signals": [], "reasoning": "stub"}

    monkeypatch.setattr(ig, "_classify_llm", _stub_llm)
    # Mid-confidence heuristic → LLM escalation fires.
    r = await ig.classify(
        "the thing we discussed earlier", db=None, user_id="u9",
    )
    assert seen["user_id"] == "u9"
    assert r["tier"] in ig.VALID_TIERS


# ══════════════════════════════════════════════════════════════════
# #2 + #3 — chat_pre_llm: ora_panel gate + fail-closed quota
# ══════════════════════════════════════════════════════════════════

from routers.chat_pre_llm import resolve_pre_llm


class _Body:
    """Minimal stand-in for the chat_send/chat_stream Pydantic body."""
    def __init__(self, prompt, session_id="s1", project_id=None, ora_panel=False):
        self.prompt = prompt
        self.session_id = session_id
        self.project_id = project_id
        self.ora_panel = ora_panel


USER_FREE = {"user_id": "u1", "email": "user@example.com", "tier": "free"}


async def _agentic_classify(*a, **kw):
    return {"tier": "agentic", "confidence": 0.9}


@pytest.mark.asyncio
async def test_ora_panel_agentic_turn_now_hits_task_gate_402(monkeypatch):
    """Leak #2 — Ask Advisor (ora_panel=True) used to skip the only
    live gate entirely. A 402 now short-circuits the panel turn too."""
    monkeypatch.setattr("core.intent_gateway.classify", _agentic_classify)

    async def _quota_exceeded(*a, **kw):
        raise HTTPException(402, detail={
            "error": "monthly_task_limit_reached", "tier": "free",
            "tasks_this_month": 10, "monthly_task_cap": 10,
            "upgrade_url": "/settings#pricing",
            "message": "You've used your 10 free fixes this month on the Free plan.",
        })
    monkeypatch.setattr("services.usage.assert_has_task_budget", _quota_exceeded)

    outcome = await resolve_pre_llm(
        db=None, user=USER_FREE, body=_Body("fix my hours page", ora_panel=True),
        bin_ctx=None, prior_fix_signal=False, prior_turn_text="",
        session_summary="", allowed_modes=["swift"], req_mode="swift",
        run_confirm_boundary=False, ora_panel=True,
    )
    assert outcome.result is not None
    assert outcome.result["provider"] == "fix-quota-exceeded"
    assert outcome.result["meta"]["quota_exceeded"] is True


@pytest.mark.asyncio
async def test_ora_panel_agentic_turn_with_quota_ok_falls_through(monkeypatch):
    """Panel turn with quota remaining still runs (result None) —
    the gate now APPLIES to the panel, it does not block it."""
    monkeypatch.setattr("core.intent_gateway.classify", _agentic_classify)
    monkeypatch.setattr("services.usage.assert_has_task_budget",
                        AsyncMock(return_value=None))

    outcome = await resolve_pre_llm(
        db=None, user=USER_FREE, body=_Body("fix my hours page", ora_panel=True),
        bin_ctx=None, prior_fix_signal=False, prior_turn_text="",
        session_summary="", allowed_modes=["swift"], req_mode="swift",
        run_confirm_boundary=False, ora_panel=True,
    )
    assert outcome.result is None
    assert outcome.tier == "agentic"


@pytest.mark.asyncio
async def test_quota_infra_failure_fails_closed_503(monkeypatch):
    """Leak #3 — a DB hiccup inside the quota check used to silently
    wave the turn through unmetered. Default is now CLOSED (503)."""
    monkeypatch.delenv("ORA_QUOTA_FAIL_OPEN", raising=False)
    monkeypatch.setattr("core.intent_gateway.classify", _agentic_classify)

    async def _infra_down(*a, **kw):
        raise RuntimeError("mongo down")
    monkeypatch.setattr("services.usage.assert_has_task_budget", _infra_down)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_pre_llm(
            db=None, user=USER_FREE, body=_Body("update my opening hours"),
            bin_ctx=None, prior_fix_signal=False, prior_turn_text="",
            session_summary="", allowed_modes=["swift"], req_mode="swift",
            run_confirm_boundary=False,
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "quota_check_unavailable"


@pytest.mark.asyncio
async def test_quota_infra_failure_open_with_kill_switch(monkeypatch):
    """Leak #3 — the emergency kill-switch restores the old
    fail-open behaviour when explicitly opted in."""
    monkeypatch.setenv("ORA_QUOTA_FAIL_OPEN", "1")
    monkeypatch.setattr("core.intent_gateway.classify", _agentic_classify)

    async def _infra_down(*a, **kw):
        raise RuntimeError("mongo down")
    monkeypatch.setattr("services.usage.assert_has_task_budget", _infra_down)

    outcome = await resolve_pre_llm(
        db=None, user=USER_FREE, body=_Body("update my opening hours"),
        bin_ctx=None, prior_fix_signal=False, prior_turn_text="",
        session_summary="", allowed_modes=["swift"], req_mode="swift",
        run_confirm_boundary=False,
    )
    assert outcome.result is None


@pytest.mark.asyncio
async def test_quota_404_lookup_error_still_falls_through(monkeypatch):
    """A non-402 HTTPException (e.g. 404 user-not-found for a
    synthetic user) is NOT an infra failure — the pre-existing
    fail-open for lookup errors is preserved, only the blanket
    `except Exception` arm went fail-closed."""
    monkeypatch.delenv("ORA_QUOTA_FAIL_OPEN", raising=False)
    monkeypatch.setattr("core.intent_gateway.classify", _agentic_classify)

    async def _user_missing(*a, **kw):
        raise HTTPException(404, "User not found")
    monkeypatch.setattr("services.usage.assert_has_task_budget", _user_missing)

    outcome = await resolve_pre_llm(
        db=None, user=USER_FREE, body=_Body("update my opening hours"),
        bin_ctx=None, prior_fix_signal=False, prior_turn_text="",
        session_summary="", allowed_modes=["swift"], req_mode="swift",
        run_confirm_boundary=False,
    )
    assert outcome.result is None


# ══════════════════════════════════════════════════════════════════
# #4 — resolve_loop_mode default flips True → False
# ══════════════════════════════════════════════════════════════════

from services.mode_routing import resolve_loop_mode


def test_resolve_loop_mode_default_is_fail_closed(monkeypatch):
    """Leak #4 — the odd one out: siblings default False, this
    defaulted True and silently escalated a free loop to Pro when
    the caller forgot the flag."""
    monkeypatch.setenv("EDIT_TIER_MODE", "gated")
    assert resolve_loop_mode("swift") == "swift"
    assert resolve_loop_mode("pro") == "pro"
    assert resolve_loop_mode("maxx") == "maxx"
    assert resolve_loop_mode(None) is None
    assert resolve_loop_mode("turbo") is None


def test_resolve_loop_mode_explicit_pro_still_escalates(monkeypatch):
    """Passing the flag explicitly is unchanged — the sole production
    caller (routers/loop.py) does, so live behaviour is unaffected."""
    monkeypatch.setenv("EDIT_TIER_MODE", "gated")
    assert resolve_loop_mode("swift", account_has_pro=True) == "pro"
    assert resolve_loop_mode("swift", account_has_pro=False) == "swift"


# ══════════════════════════════════════════════════════════════════
# #5 — per-user monthly cap called in _call_llm_with_meta_impl
# ══════════════════════════════════════════════════════════════════

from services.llm import _meta


class _FakeDevUsers:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, filt, proj=None):
        return dict(self._doc) if self._doc else None


class _FakeMetaDB:
    def __init__(self, user_doc):
        self.dev_users = _FakeDevUsers(user_doc)


@contextmanager
def _no_trace(*a, **kw):
    yield {"success": lambda *a, **k: None}


def _patch_meta_path(monkeypatch, *, user_doc, monthly_cap):
    """Wire _call_llm_with_meta so ONLY the monthly-cap block is
    live: global breaker no-op, inner LLM stubbed, tracing off."""
    import cto_services.db as _dbmod
    import services.brain_gateway as _brain
    import services.langfuse_tracing as _lf
    import services.llm_cost_breaker as _breaker
    import services.ora_chat_v2.llm_client as _mock_mod

    monkeypatch.setattr(_dbmod, "get_db", lambda: _FakeMetaDB(user_doc))
    monkeypatch.setattr(_breaker, "assert_within_cap",
                        AsyncMock(return_value=None))
    monkeypatch.setattr(_breaker, "assert_within_user_monthly_cap", monthly_cap)
    monkeypatch.setattr(_breaker, "record_cost", AsyncMock(return_value=None))
    monkeypatch.setattr(_lf, "trace_llm_call", _no_trace)
    monkeypatch.setattr(_mock_mod, "is_mock", lambda: False)
    # Brain recorder OFF: its detached drain worker is loop-bound and
    # hangs a second call_llm_with_meta in the same pytest process
    # (pytest-asyncio gives each test a fresh loop; the worker from
    # test 1 is bound to the closed loop — pre-existing repo
    # isolation issue, not the cap under test; F2c calls the wrapper
    # only once per process and never hits it).
    async def _no_record(**kw):
        return "bc_test"
    monkeypatch.setattr(_brain, "record_call", _no_record)

    async def _fake_inner(**kw):
        return {"ok": True, "provider": "deepseek", "content": "hi",
                "model": "deepseek-chat"}
    monkeypatch.setattr(_meta, "_call_llm_with_meta_inner", _fake_inner)


@pytest.mark.asyncio
async def test_monthly_cap_called_for_non_founder(monkeypatch):
    """Leak #5 — the $6.50/user/month cap had zero call sites. A
    paid (pro) user's call now goes through it."""
    calls = []

    async def _monthly_cap(db, *, user_id, est_cost_usd=0.0):
        calls.append((user_id, est_cost_usd))

    _patch_meta_path(monkeypatch, user_doc={"tier": "pro", "email": "p@x.com"},
                     monthly_cap=_monthly_cap)
    result = await _meta.call_llm_with_meta(
        "system prompt", "user msg", max_tokens=100, user_id="u-pro",
    )
    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0][0] == "u-pro"


@pytest.mark.asyncio
async def test_monthly_cap_not_called_for_founder(monkeypatch):
    """Founders are exempt — the cap must not gate the founder's
    own turns."""
    calls = []

    async def _monthly_cap(db, *, user_id, est_cost_usd=0.0):
        calls.append(user_id)

    _patch_meta_path(monkeypatch,
                     user_doc={"tier": "founder", "email": "f@x.com"},
                     monthly_cap=_monthly_cap)
    result = await _meta.call_llm_with_meta(
        "system prompt", "user msg", max_tokens=100, user_id="u-founder",
    )
    assert result["ok"] is True
    assert calls == []


@pytest.mark.asyncio
async def test_monthly_cap_breach_returns_ok_false_user_monthly(monkeypatch):
    """A breached monthly cap converts to the graceful {"ok": False}
    shape with the distinguishable COST_CAP_USER_MONTHLY code — no
    raw raise reaches callers."""
    async def _boom(db, *, user_id, est_cost_usd=0.0):
        raise HTTPException(429, {
            "error": "llm_cost_cap_hit",
            "error_code": "COST_CAP_USER_MONTHLY",
            "message": "You've reached this month's usage cap for your plan.",
        })

    _patch_meta_path(monkeypatch, user_doc={"tier": "pro", "email": "p@x.com"},
                     monthly_cap=_boom)
    result = await _meta.call_llm_with_meta(
        "system prompt", "user msg", max_tokens=100, user_id="u-pro",
    )
    assert result["ok"] is False
    assert result["error_code"] == "COST_CAP_USER_MONTHLY"
    assert "HTTPException" not in result["error"]
    assert "Traceback" not in result["error"]


@pytest.mark.asyncio
async def test_monthly_cap_skipped_without_user_id(monkeypatch):
    """No user_id (system/background calls) → the cap is skipped,
    exactly like the global breaker path with no user."""
    calls = []

    async def _monthly_cap(db, *, user_id, est_cost_usd=0.0):
        calls.append(user_id)

    _patch_meta_path(monkeypatch, user_doc={"tier": "pro", "email": "p@x.com"},
                     monthly_cap=_monthly_cap)
    result = await _meta.call_llm_with_meta(
        "system prompt", "user msg", max_tokens=100,
    )
    assert result["ok"] is True
    assert calls == []


# ══════════════════════════════════════════════════════════════════
# #6 — review_mode from the caller's plan, not hardcoded "pro"
# ══════════════════════════════════════════════════════════════════

import services.finding_fix_applier as ffa


@pytest.mark.parametrize("tier,expected", [
    (None, "swift"),
    ("free", "swift"),
    ("starter", "swift"),
    ("pro", "pro"),
    ("team", "pro"),
    ("founder", "pro"),
    ("bogus-tier", "swift"),
])
def test_review_mode_for_user(tier, expected):
    """Leak #6 — Starter ($9, modes ["swift"]) fixes used to route
    through the Pro model chain. The plan now decides; unknown tier
    fails closed to swift."""
    user = {"user_id": "u1"} if tier is None else {"user_id": "u1", "tier": tier}
    assert ffa._review_mode_for_user(user) == expected
    assert ffa._review_mode_for_user(None) == "swift"


@pytest.mark.asyncio
async def test_generate_patched_content_passes_plan_review_mode(monkeypatch):
    """The single-finding path forwards the given review_mode to
    call_llm_with_meta (was hardcoded "pro")."""
    seen = {}

    async def _fake_llm(**kwargs):
        seen["review_mode"] = kwargs.get("review_mode")
        return {"content": "patched\n"}

    monkeypatch.setattr("services.llm.call_llm_with_meta",
                        AsyncMock(side_effect=_fake_llm))
    patched, err = await ffa._generate_patched_content(
        path="a.py", current_content="orig\n",
        finding={"rule_id": "r1", "severity": "high"},
        user_id="u1", db=None, review_mode="swift",
    )
    assert err is None
    assert patched.strip() == "patched"
    assert seen["review_mode"] == "swift"


@pytest.mark.asyncio
async def test_generate_patched_content_default_is_swift(monkeypatch):
    """Omitting review_mode fails closed to "swift" — never "pro"
    by default."""
    seen = {}

    async def _fake_llm(**kwargs):
        seen["review_mode"] = kwargs.get("review_mode")
        return {"content": "patched\n"}

    monkeypatch.setattr("services.llm.call_llm_with_meta",
                        AsyncMock(side_effect=_fake_llm))
    _, err = await ffa._generate_patched_content(
        path="a.py", current_content="orig\n",
        finding={"rule_id": "r1", "severity": "high"},
        user_id="u1", db=None,
    )
    assert err is None
    assert seen["review_mode"] == "swift"


@pytest.mark.asyncio
async def test_generate_patched_content_multi_passes_plan_review_mode(monkeypatch):
    """Same rule for the multi-finding path (was the second hardcoded
    "pro" call site)."""
    seen = {}

    async def _fake_llm(**kwargs):
        seen["review_mode"] = kwargs.get("review_mode")
        return {"content": "patched\n"}

    monkeypatch.setattr("services.llm.call_llm_with_meta",
                        AsyncMock(side_effect=_fake_llm))
    patched, err = await ffa._generate_patched_content_multi(
        path="a.py", current_content="orig\n",
        findings=[{"rule_id": "r1", "severity": "high"}],
        user_id="u1", db=None, review_mode="swift",
    )
    assert err is None
    assert patched.strip() == "patched"
    assert seen["review_mode"] == "swift"


# ── end-to-end threading through apply_file_fixes / apply_finding_fix ──

class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs if docs is not None else []

    async def find_one(self, filt, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                return dict(d)
        return None

    async def insert_one(self, row):
        self.docs.append(dict(row))

    async def update_one(self, filt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                d.update(update.get("$set", {}))
                return

    async def update_many(self, filt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                d.update(update.get("$set", {}))

    def find(self, filt, proj=None):
        matched = [dict(d) for d in self.docs
                   if all(d.get(k) == v for k, v in (filt or {}).items())]

        class _Cur:
            def __init__(self, items):
                self.items = items

            async def to_list(self, length=None):
                return list(self.items)[:(length if length is not None else len(self.items))]
        return _Cur(matched)

    async def delete_many(self, filt):
        self.docs = [d for d in self.docs
                     if not all(d.get(k) == v for k, v in (filt or {}).items())]


class _FakeFixBatches(_FakeColl):
    async def find_one_and_update(self, filt, update, return_document=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                for k, v in update.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                for k, v in update.get("$push", {}).items():
                    if isinstance(v, dict) and "$each" in v:
                        d.setdefault("rows", []).extend(v["$each"])
                    else:
                        d.setdefault("rows", []).append(v)
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                return dict(d)
        return None


class _FakeFixDB:
    def __init__(self):
        self.cto_projects = _FakeColl([{
            "project_id": "p1", "user_id": "u1",
            "github_owner": "o", "github_repo": "r",
            "github_branch": "main",
        }])
        self.finding_fixes = _FakeColl()
        self.fix_batches = _FakeFixBatches()
        self.cto_open_findings = _FakeColl()
        self.fixed_findings = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture(autouse=True)
def _reset_batch_locks():
    import services.fix_batches as fb
    fb._batch_locks.clear()
    yield
    fb._batch_locks.clear()


def _mock_pat(monkeypatch):
    async def _get_token(proj):
        return "ghp_tok", None, None
    monkeypatch.setattr("services.pat_vault.get_repo_token_or_error", _get_token)


def _mock_batch_github(monkeypatch):
    import services.loop_safety as ls

    async def _create_branch(**kw):
        return True, None

    async def _open_pr(**kw):
        return {"ok": True, "pr_url": "https://github.com/o/r/pull/1",
                "pr_number": 1, "reused": False, "error": None}

    async def _add_label(**kw):
        return True, None

    async def _update_body(**kw):
        return True, None

    async def _get_status(**kw):
        return {"ok": True, "merged": False, "state": "open",
                "merge_commit_sha": None}

    monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)
    monkeypatch.setattr(ls, "open_or_reuse_pr", _open_pr)
    monkeypatch.setattr(ls, "add_pr_label", _add_label)
    monkeypatch.setattr(ls, "update_pr_body", _update_body)
    monkeypatch.setattr(ls, "get_pr_status", _get_status)


def _present_by_marker(patched_content, path, finding):
    marker = f"FIXED[{finding['rule_id']}]"
    return marker not in patched_content


@pytest.mark.asyncio
async def test_apply_file_fixes_threads_starter_plan_as_swift(monkeypatch):
    """Leak #6 end-to-end (multi path): a Starter user's file fix
    must reach the LLM as review_mode="swift", not "pro"."""
    _mock_pat(monkeypatch)
    _mock_batch_github(monkeypatch)
    db = _FakeFixDB()

    async def _fetch(owner, repo, branch, path, token):
        return "orig content with BAD_A", None
    monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

    seen = {}

    async def _llm_multi(*, path, current_content, findings, user_id, db,
                         review_mode=None):
        seen["review_mode"] = review_mode
        out = current_content
        for f in findings:
            out += f"\nFIXED[{f['rule_id']}]"
        return out, None
    monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
    monkeypatch.setattr(ffa, "_finding_still_present", _present_by_marker)

    async def _commit(**kw):
        return {"sha": "s1", "full_sha": "s1" * 20, "html_url": "https://x"}
    monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

    res = await ffa.apply_file_fixes(
        db=db, user={"user_id": "u1", "tier": "starter"}, project_id="p1",
        file="a.py",
        findings=[{"id": "fa", "rule_id": "rule_a", "file": "a.py",
                   "line": 1, "severity": "high"}],
    )
    assert res["ok"] is True
    assert seen["review_mode"] == "swift"


@pytest.mark.asyncio
async def test_apply_file_fixes_threads_pro_plan_as_pro(monkeypatch):
    """Leak #6 end-to-end (multi path): a Pro user's fix keeps
    review_mode="pro" — the fix removes the leak, not the feature."""
    _mock_pat(monkeypatch)
    _mock_batch_github(monkeypatch)
    db = _FakeFixDB()

    async def _fetch(owner, repo, branch, path, token):
        return "orig content with BAD_A", None
    monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

    seen = {}

    async def _llm_multi(*, path, current_content, findings, user_id, db,
                         review_mode=None):
        seen["review_mode"] = review_mode
        out = current_content
        for f in findings:
            out += f"\nFIXED[{f['rule_id']}]"
        return out, None
    monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
    monkeypatch.setattr(ffa, "_finding_still_present", _present_by_marker)

    async def _commit(**kw):
        return {"sha": "s1", "full_sha": "s1" * 20, "html_url": "https://x"}
    monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

    res = await ffa.apply_file_fixes(
        db=db, user={"user_id": "u1", "tier": "pro"}, project_id="p1",
        file="a.py",
        findings=[{"id": "fa", "rule_id": "rule_a", "file": "a.py",
                   "line": 1, "severity": "high"}],
    )
    assert res["ok"] is True
    assert seen["review_mode"] == "pro"


@pytest.mark.asyncio
async def test_apply_finding_fix_threads_starter_plan_as_swift(monkeypatch):
    """Leak #6 end-to-end (single path): a Starter user's single
    fix must reach the LLM as review_mode="swift"."""
    _mock_pat(monkeypatch)
    _mock_batch_github(monkeypatch)
    db = _FakeFixDB()

    async def _fetch(owner, repo, branch, path, token):
        return "orig content with BAD_A", None
    monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)

    seen = {}

    async def _llm(*, path, current_content, finding, user_id, db,
                   review_mode=None):
        seen["review_mode"] = review_mode
        return current_content + f"\nFIXED[{finding['rule_id']}]", None
    monkeypatch.setattr(ffa, "_generate_patched_content", _llm)

    # First presence call (pre-check) says present; post-patch calls
    # say resolved — mirrors the graph-refresh test's counter.
    _calls = {"n": 0}

    def _still_present(*a, **k):
        _calls["n"] += 1
        return _calls["n"] == 1
    monkeypatch.setattr(ffa, "_finding_still_present", _still_present)

    async def _commit(**kw):
        return {"sha": "s1", "full_sha": "s1" * 20, "html_url": "https://x"}
    monkeypatch.setattr("services.github_api_writer.commit_files", _commit)

    res = await ffa.apply_finding_fix(
        db=db, user={"user_id": "u1", "tier": "starter"}, project_id="p1",
        finding={"id": "fa", "rule_id": "rule_a", "file": "a.py",
                 "line": 1, "severity": "high"},
    )
    assert res["ok"] is True
    assert seen["review_mode"] == "swift"
