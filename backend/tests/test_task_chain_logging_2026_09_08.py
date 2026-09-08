"""
test_task_chain_logging_2026_09_08.py — Chain-of-thought pattern
analysis / "self-improving loop" (2026-09-08).

No mocks for the DB layer — writes to the real test Mongo and reads
it back, matching the established convention in
tests/test_iter123b_skill_usage_analytics.py.

Covers both specs given by the founder:
  1. task_chains structured logging + /admin/failure-patterns.
  2. COT persistence (failure-only, truncated, redacted) riding on
     the SAME task_chains record via `failure_thoughts_truncated`.
"""
import os
import asyncio
import pytest
import httpx

from motor.motor_asyncio import AsyncIOMotorClient

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def _db():
    mongo_url = os.environ.get("MONGO_URL") or "mongodb://localhost:27017"
    client = AsyncIOMotorClient(mongo_url)
    test_db = client[os.environ.get("DB_NAME") or "aurem_dev"]
    return client, test_db


# ── classify_task_type — simple keyword heuristic ──────────────────────

def test_classify_task_type_buckets():
    from services.task_chain_logging import classify_task_type

    assert classify_task_type("fix the login bug where users cant sign in") == "bug-fix"
    assert classify_task_type("scan the whole repo and list every file") == "scan"
    assert classify_task_type("refactor this messy component into smaller pieces") == "refactor"
    assert classify_task_type("add a dark mode toggle to settings") == "feature"
    assert classify_task_type("what does this app do") == "other"


# ── t_task_chain_truncated ─────────────────────────────────────────────

def test_task_chain_truncated_helper():
    from services.task_chain_logging import _truncate
    long = "x" * 5000
    out = _truncate(long, 200)
    assert len(out) <= 220
    assert out.startswith("x" * 50)
    short = "hello"
    assert _truncate(short, 200) == "hello"


@pytest.mark.asyncio
async def test_task_chain_logged_fields_and_truncation():
    """t_task_chain_logged + t_task_chain_truncated (integration):
    log_task_chain() with an over-long prompt/plan/response must
    persist ONE record with every required field, all correctly
    truncated to the spec'd limits."""
    from services.task_chain_logging import log_task_chain

    client, db = _db()
    marker = "tc-test-marker-A"
    try:
        await db.task_chains.delete_many({"session_id": marker})

        long_prompt = "fix " + ("the login bug " * 40)          # > 200 chars
        long_response = "y" * 3000                               # > 500 chars
        await log_task_chain(
            db, user_id="u1", session_id=marker, project_id="p1",
            user_prompt=long_prompt,
            tool_calls=[{"tool": f"tool_{i}"} for i in range(15)],  # > 10
            final_response=long_response,
            turn_state="success",
        )

        doc = await db.task_chains.find_one({"session_id": marker})
        assert doc is not None, "log_task_chain did not persist a record"
        assert doc["task_type"] == "bug-fix"
        assert len(doc["user_prompt"]) <= 220
        assert len(doc["final_response"]) <= 520
        assert len(doc["tool_calls"]) <= 10
        assert len(doc["tool_sequence"]) <= 10
        assert doc["success"] is True
        assert doc["failure_reason"] is None
        assert doc["success_pattern"]
        assert doc["ts"]

        await db.task_chains.delete_many({"session_id": marker})
    finally:
        client.close()


# ── t_task_chain_no_sensitive_data ─────────────────────────────────────

def test_redact_secrets_masks_known_shapes():
    from services.task_chain_logging import redact_secrets

    cases = [
        "here is the key AKIAABCDEFGHIJKLMNOP for aws",
        "use sk-abcdefghijklmnopqrstuvwxyz123456789012 to call openai",
        "token ghp_abcdefghijklmnopqrstuvwxyz1234567890 for github",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnop",
        "api_key: 'sk-verysecretvalue12345678'",
    ]
    for c in cases:
        out = redact_secrets(c)
        assert "[REDACTED]" in out, f"failed to redact: {c!r} -> {out!r}"


@pytest.mark.asyncio
async def test_task_chain_no_sensitive_data_end_to_end():
    """A prompt/response containing an obvious secret must never land
    in task_chains unredacted."""
    from services.task_chain_logging import log_task_chain

    client, db = _db()
    marker = "tc-test-marker-secret"
    try:
        await db.task_chains.delete_many({"session_id": marker})
        secret = "sk-thisisaveryrealsecretlookingkey1234567890"
        await log_task_chain(
            db, user_id="u1", session_id=marker, project_id=None,
            user_prompt=f"use this key {secret} to call the api",
            tool_calls=[], final_response=f"done, used {secret}",
            turn_state="success",
        )
        doc = await db.task_chains.find_one({"session_id": marker})
        assert doc is not None
        assert secret not in doc["user_prompt"]
        assert secret not in doc["final_response"]
        assert "[REDACTED]" in doc["user_prompt"]
        await db.task_chains.delete_many({"session_id": marker})
    finally:
        client.close()


# ── t_cot_logged_on_failure_only ────────────────────────────────────────

@pytest.mark.asyncio
async def test_cot_logged_on_failure_only():
    """On failure: failure_thoughts_truncated IS attached (redacted,
    each thought capped at 2000 chars). On success: it must be ABSENT
    entirely — metadata-only, no reasoning text persisted (storage/
    cost waste per the founder's explicit DO-NOT)."""
    from services.task_chain_logging import log_task_chain

    client, db = _db()
    marker_fail = "tc-test-marker-fail"
    marker_ok = "tc-test-marker-ok"
    try:
        await db.task_chains.delete_many({"session_id": {"$in": [marker_fail, marker_ok]}})

        thoughts = [
            {"thought": "First I tried X but it didn't compile"},
            {"thought": "Revising: maybe the issue is Y instead. api_key=sk-shouldnotleak12345678"},
        ]

        # Failure case.
        await log_task_chain(
            db, user_id="u1", session_id=marker_fail, project_id=None,
            user_prompt="fix the flaky test", tool_calls=[{"tool": "run_tests"}],
            final_response="ran out of time, here's what I found so far",
            turn_state="partial", failure_mode="stream_idle_timeout",
            sequential_thoughts=thoughts,
        )
        fail_doc = await db.task_chains.find_one({"session_id": marker_fail})
        assert fail_doc is not None
        assert fail_doc["success"] is False
        assert fail_doc["failure_reason"] == "stream_idle_timeout"
        assert "failure_thoughts_truncated" in fail_doc
        assert len(fail_doc["failure_thoughts_truncated"]) == 2
        # Secret inside a thought must still be redacted.
        assert "sk-shouldnotleak12345678" not in fail_doc["failure_thoughts_truncated"][1]
        assert "[REDACTED]" in fail_doc["failure_thoughts_truncated"][1]

        # Success case — SAME sequential_thoughts passed in, but must
        # NOT end up persisted at all.
        await log_task_chain(
            db, user_id="u1", session_id=marker_ok, project_id=None,
            user_prompt="fix the flaky test", tool_calls=[{"tool": "run_tests"}],
            final_response="fixed it, tests pass now",
            turn_state="success",
            sequential_thoughts=thoughts,
        )
        ok_doc = await db.task_chains.find_one({"session_id": marker_ok})
        assert ok_doc is not None
        assert ok_doc["success"] is True
        assert "failure_thoughts_truncated" not in ok_doc

        await db.task_chains.delete_many({"session_id": {"$in": [marker_fail, marker_ok]}})
    finally:
        client.close()


# ── t_cot_truncated ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cot_truncated_to_2000_chars():
    from services.task_chain_logging import log_task_chain

    client, db = _db()
    marker = "tc-test-marker-cot-long"
    try:
        await db.task_chains.delete_many({"session_id": marker})
        long_thought = "z" * 5000
        await log_task_chain(
            db, user_id="u1", session_id=marker, project_id=None,
            user_prompt="fix something", tool_calls=[],
            final_response="couldn't finish",
            turn_state="error", failure_mode="provider_5xx",
            sequential_thoughts=[{"thought": long_thought}],
        )
        doc = await db.task_chains.find_one({"session_id": marker})
        assert doc is not None
        assert len(doc["failure_thoughts_truncated"][0]) <= 2020
        await db.task_chains.delete_many({"session_id": marker})
    finally:
        client.close()


# ── t_cot_no_secrets — covered above (secret inside a thought) plus a
# focused unit check ────────────────────────────────────────────────

def test_cot_no_secrets_unit():
    from services.task_chain_logging import redact_secrets
    thought = "Let me use AKIAABCDEFGHIJKLMNOP to check the s3 bucket"
    assert "[REDACTED]" in redact_secrets(thought)
    assert "AKIAABCDEFGHIJKLMNOP" not in redact_secrets(thought)


# ── log_task_chain never raises (best-effort logging contract) ─────────

@pytest.mark.asyncio
async def test_log_task_chain_never_raises_without_db():
    from services.task_chain_logging import log_task_chain
    # db=None must be a silent no-op, never propagate.
    await log_task_chain(
        None, user_id="u1", session_id="s1", project_id=None,
        user_prompt="x", tool_calls=[], final_response="y",
        turn_state="success",
    )


# ── Orchestrator wiring — sequential_thoughts flows through result ─────

def test_orchestrator_exposes_sequential_thoughts_in_result():
    with open("/app/backend/services/orchestrator.py") as f:
        src = f.read()
    assert src.count('"sequential_thoughts": local_ctx.get("sequential_thoughts")') >= 4, \
        "sequential_thoughts must be exposed on every major chat_with_tools() exit path"


def test_stream_and_turn_and_watchdog_call_log_task_chain():
    for path in (
        "/app/backend/routers/chat/stream.py",
        "/app/backend/routers/chat/turn.py",
        "/app/backend/routers/chat/watchdog.py",
    ):
        with open(path) as f:
            src = f.read()
        assert "log_task_chain" in src, f"{path} must call log_task_chain"


# ── t_failure_patterns_endpoint / t_failure_patterns_tab ────────────────

@pytest.mark.asyncio
async def test_failure_patterns_endpoint_requires_admin():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{API_URL}/api/aurem-dev/admin/failure-patterns")
        assert r.status_code == 401, f"expected 401, got {r.status_code}"


@pytest.mark.asyncio
async def test_failure_patterns_endpoint_route_exists():
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{API_URL}/api/aurem-dev/admin/failure-patterns",
            headers={"Authorization": "Bearer fake-jwt-for-route-check"},
        )
        assert r.status_code in (401, 403), \
            f"route missing, got {r.status_code}: {r.text[:200]}"


@pytest.mark.asyncio
async def test_failure_patterns_endpoint_shape_and_click_through():
    """Seed a few task_chains docs directly, hit the real admin login,
    then call /admin/failure-patterns and verify: top-failed task
    types/patterns are aggregated correctly, tool-sequence rates are
    computed, and the click-through sample includes the truncated
    failure reasoning text (not the raw untruncated thought)."""
    from datetime import datetime, timezone

    client, db = _db()
    marker = "tc-test-marker-endpoint"
    try:
        await db.task_chains.delete_many({"session_id": marker})
        now = datetime.now(timezone.utc).timestamp()
        docs = [
            {"user_id": "u1", "session_id": marker, "project_id": None,
             "task_type": "bug-fix", "user_prompt": "fix the login flow",
             "model_plan": "p", "tool_calls": ["read_file"], "tool_sequence": ["read_file"],
             "final_response": "r", "success": False,
             "failure_reason": "stream_idle_timeout",
             "failure_thoughts_truncated": ["thought text truncated here"],
             "ts": now},
            {"user_id": "u1", "session_id": marker, "project_id": None,
             "task_type": "bug-fix", "user_prompt": "fix another bug",
             "model_plan": "p", "tool_calls": ["read_file"], "tool_sequence": ["read_file"],
             "final_response": "r", "success": False,
             "failure_reason": "stream_idle_timeout", "ts": now},
            {"user_id": "u1", "session_id": marker, "project_id": None,
             "task_type": "feature", "user_prompt": "add dark mode",
             "model_plan": "p", "tool_calls": [], "tool_sequence": [],
             "final_response": "r", "success": True,
             "failure_reason": None, "success_pattern": "ok", "ts": now},
        ]
        await db.task_chains.insert_many(docs)

        token_resp = httpx.post(
            f"{API_URL}/api/aurem-dev/auth/login",
            json={"email": "test@aurem.dev", "password": "AuremTest2026!"},
            timeout=30,
        )
        token = token_resp.json().get("token")
        assert token, f"login failed: {token_resp.status_code} {token_resp.text[:200]}"

        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{API_URL}/api/aurem-dev/admin/failure-patterns",
                headers={"Authorization": f"Bearer {token}"},
                params={"days": 1},
                timeout=30,
            )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert "top_failed_task_types" in body
        assert "tool_sequence_rates" in body
        assert "sample_failures" in body
        assert "suggestions" in body

        failed_types = {row["value"]: row["count"] for row in body["top_failed_task_types"]}
        assert failed_types.get("bug-fix", 0) >= 2

        # Click-through must surface the truncated reasoning text for
        # at least one sample, never a bare absence of the field.
        sample_texts = [
            s.get("failure_thoughts_truncated") for s in body["sample_failures"]
            if s.get("failure_thoughts_truncated")
        ]
        assert sample_texts, "expected at least one sample failure with click-through reasoning text"

        await db.task_chains.delete_many({"session_id": marker})
    finally:
        client.close()
