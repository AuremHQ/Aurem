"""
test_master_task_2026_09_27_unified_ledger.py

MASTER TASK (2026-09-27) Part A/B regression tests — the ONE usage &
cost ledger for every LLM call in the app.

Guarantees under test:
  1. `log_real_usage` writes ONE row to `real_llm_usage` with the full
     schema: user_id, project_id, task_id, session_id, surface, mode,
     provider, model, input_tokens, output_tokens, cached_tokens,
     cost_usd, latency_ms, status, error_reason, created_at.
  2. Real provider `usage.cost` is used when present; otherwise cost
     is computed from `services.pricing_table` (per-million rates) —
     never a hardcoded per-1k rate.
  3. Zero-token SUCCESS calls are dropped (nothing real to log);
     zero-token FAILED calls are KEPT with a real `error_reason` —
     this is what fixes "unknown" failure reasons.
  4. `llm_surface_context`/`current_surface_context` nesting: a
     mode-only override never wipes an outer surface tag; an outer
     Loop's task_id survives a nested Parliament call that only
     overrides `surface`.
  5. `tag_request_surface` (the whole-request variant used by
     chat_stream/security_scan) sets the ambient surface without a
     context-manager wrapper.
  6. `pricing_table.compute_cost_usd` sanity check — 3,575 DeepSeek
     tokens must cost about a tenth of a cent, not $1.07 (MASTER TASK
     Part F #26 — the literal bug this fixes).
  7. `_rollup_task_llm_usage` (services/cto_projects_helpers.py) sums
     a task's real LLM spend onto the task document at completion,
     and fills a real `error_reason` for a failed task that has none.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import loop_token_ledger as ledger    # noqa: E402
from services import pricing_table                  # noqa: E402


class _MockColl:
    def __init__(self):
        self.docs: list[dict] = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": "x"})()

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [d for d in self.docs if self._match(d, query or {})]
        if sort:
            key, direction = sort[0]
            matched.sort(key=lambda r: r.get(key, 0), reverse=(direction < 0))
        return dict(matched[0]) if matched else None

    def aggregate(self, pipeline):
        match = next((s["$match"] for s in pipeline if "$match" in s), {})
        rows = [d for d in self.docs if self._match(d, match)]
        group = next((s["$group"] for s in pipeline if "$group" in s), None)
        if not group:
            return _AggCursor(rows)
        out = {"_id": None}
        for fname, acc in group.items():
            if fname == "_id":
                continue
            if "$sum" in acc:
                field = acc["$sum"]
                if field == 1:
                    out[fname] = len(rows)
                else:
                    out[fname] = sum(float(r.get(field.lstrip("$"), 0) or 0) for r in rows)
            elif "$addToSet" in acc:
                field = acc["$addToSet"].lstrip("$")
                out[fname] = list({r.get(field) for r in rows})
        return _AggCursor([out] if rows else [])

    @staticmethod
    def _match(doc, query):
        for k, v in query.items():
            if isinstance(v, dict):
                if "$gte" in v and doc.get(k, 0) < v["$gte"]:
                    return False
            elif doc.get(k) != v:
                return False
        return True


class _AggCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._docs:
            raise StopAsyncIteration
        return self._docs.pop(0)

    async def to_list(self, n):
        return self._docs[:n]


class _MockDB:
    def __init__(self):
        self.real_llm_usage = _MockColl()
        self.cto_tasks = _MockColl()

    def __getattr__(self, name):
        if not hasattr(self, "_extra"):
            object.__setattr__(self, "_extra", {})
        if name not in self._extra:
            self._extra[name] = _MockColl()
        return self._extra[name]


@pytest_asyncio.fixture(autouse=True)
async def _mock_db(monkeypatch):
    mock = _MockDB()
    import cto_services.db as dbmod
    monkeypatch.setattr(dbmod, "get_db", lambda: mock)
    yield mock


# ── 1/2/3 — log_real_usage schema + real cost + failed-row keep ──────

@pytest.mark.asyncio
async def test_log_real_usage_writes_full_schema_with_real_provider_cost(_mock_db):
    await ledger.log_real_usage(
        "anthropic/claude-sonnet-4.5",
        {"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.0033},
        surface="parliament", mode="vote", task_id="t-1",
        project_id="p-1", session_id="s-1", latency_ms=842.5,
    )
    assert len(_mock_db.real_llm_usage.docs) == 1
    row = _mock_db.real_llm_usage.docs[0]
    assert row["user_id"] == "anonymous"
    assert row["project_id"] == "p-1"
    assert row["task_id"] == "t-1"
    assert row["session_id"] == "s-1"
    assert row["surface"] == "parliament"
    assert row["mode"] == "vote"
    assert row["provider"] == "anthropic"
    assert row["model"] == "anthropic/claude-sonnet-4.5"
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 200
    assert row["cached_tokens"] == 0
    assert row["cost_usd"] == 0.0033          # real provider-reported cost, used as-is
    assert row["latency_ms"] == 842.5
    assert row["status"] == "ok"
    assert row["error_reason"] is None
    assert "created_at" in row


@pytest.mark.asyncio
async def test_log_real_usage_falls_back_to_pricing_table_when_no_provider_cost(_mock_db):
    # Groq/DeepSeek-direct paths never return a `.cost` field.
    await ledger.log_real_usage(
        "deepseek-v4-flash", {"prompt_tokens": 3575, "completion_tokens": 0},
        provider="deepseek_direct", status="ok",
    )
    row = _mock_db.real_llm_usage.docs[0]
    expected = pricing_table.compute_cost_usd("deepseek-v4-flash", 3575, 0, 0)
    assert row["cost_usd"] == round(expected, 8)
    # MASTER TASK Part F #26 sanity check — a tenth of a cent, not $1.07.
    assert 0.0001 < row["cost_usd"] < 0.01


@pytest.mark.asyncio
async def test_zero_token_success_dropped_but_failure_kept_with_real_reason(_mock_db):
    await ledger.log_real_usage("z-ai/glm-5.2", {}, status="ok")
    assert _mock_db.real_llm_usage.docs == []

    await ledger.log_real_usage(
        "z-ai/glm-5.2", {}, status="failed",
        error_reason="HTTPStatusError: 429 rate limited",
        surface="loop", mode="execute",
    )
    assert len(_mock_db.real_llm_usage.docs) == 1
    row = _mock_db.real_llm_usage.docs[0]
    assert row["status"] == "failed"
    assert row["error_reason"] == "HTTPStatusError: 429 rate limited"
    assert row["input_tokens"] == 0 and row["cost_usd"] == 0.0


# ── 4 — surface/mode context nesting ──────────────────────────────────

@pytest.mark.asyncio
async def test_mode_only_override_does_not_wipe_outer_surface(_mock_db):
    async with ledger.llm_surface_context(surface="cto_chat", session_id="sess-9"):
        async with ledger.llm_surface_context(mode="title"):
            ctx = ledger.current_surface_context()
            assert ctx["surface"] == "cto_chat"     # untouched by inner mode-only call
            assert ctx["mode"] == "title"
            assert ctx["session_id"] == "sess-9"
        # inner mode reset on exit
        ctx_after = ledger.current_surface_context()
        assert ctx_after["mode"] is None


@pytest.mark.asyncio
async def test_nested_parliament_call_keeps_loop_task_id(_mock_db):
    async with ledger.loop_call_context(loop_id="loop-77", phase_tag="execute", user_id="u1"):
        async with ledger.llm_surface_context(surface="parliament", mode="vote"):
            ctx = ledger.current_surface_context()
            assert ctx["surface"] == "parliament"         # overridden
            assert ctx["task_id"] == "loop-77"             # still falls back to the loop
        # back to loop-level surface fallback
        ctx_outer = ledger.current_surface_context()
        assert ctx_outer["surface"] == "loop"
        assert ctx_outer["mode"] == "execute"


@pytest.mark.asyncio
async def test_tag_request_surface_sets_ambient_context_without_wrapper(_mock_db):
    async def _one_request():
        ledger.tag_request_surface("scan", project_id="proj-5")
        return ledger.current_surface_context()

    ctx = await _one_request()
    assert ctx["surface"] == "scan"
    assert ctx["project_id"] == "proj-5"


# ── 6 — pricing table sanity (Part F #26) ─────────────────────────────

def test_pricing_table_deepseek_3575_tokens_is_a_tenth_of_a_cent():
    cost = pricing_table.compute_cost_usd("deepseek/deepseek-chat", 3575, 0, 0)
    assert 0.0005 < cost < 0.002, f"expected ~a tenth of a cent, got ${cost}"
    # The old bug's implied rate ($0.30/1k => $1.07 for 3575 in-tokens
    # alone) must be nowhere close.
    assert cost < 1.07 / 100


# ── 7 — task-completion rollup ────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollup_task_llm_usage_sums_tokens_and_cost_onto_task(_mock_db):
    from services.cto_projects_helpers import _rollup_task_llm_usage

    await _mock_db.real_llm_usage.insert_one({
        "task_id": "task-42", "total_tokens": 500, "cost_usd": 0.01,
        "mode": "plan", "model": "deepseek/deepseek-chat", "status": "ok", "ts": 1,
    })
    await _mock_db.real_llm_usage.insert_one({
        "task_id": "task-42", "total_tokens": 300, "cost_usd": 0.02,
        "mode": "execute", "model": "anthropic/claude-sonnet-4.5", "status": "ok", "ts": 2,
    })

    fields = {"status": "done"}
    await _rollup_task_llm_usage(_mock_db, "task-42", fields)

    assert fields["tokens_used"] == 800
    assert round(fields["cost_usd"], 4) == 0.03
    assert fields["llm_call_count"] == 2
    assert set(fields["llm_modes"]) == {"plan", "execute"}


@pytest.mark.asyncio
async def test_rollup_fills_error_reason_for_failed_task_with_none_set(_mock_db):
    from services.cto_projects_helpers import _rollup_task_llm_usage

    await _mock_db.real_llm_usage.insert_one({
        "task_id": "task-99", "total_tokens": 0, "cost_usd": 0.0,
        "status": "failed", "error_reason": "chain_exhausted: ConnectError: timeout",
        "ts": 5,
    })

    fields = {"status": "failed"}   # no `error`/`error_code` set by the caller
    await _rollup_task_llm_usage(_mock_db, "task-99", fields)

    assert fields["error_reason"] == "chain_exhausted: ConnectError: timeout"
