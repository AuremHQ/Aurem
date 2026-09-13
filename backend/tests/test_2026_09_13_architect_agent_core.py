"""
tests/test_2026_09_13_architect_agent_core.py

Phase 2 (Architect Agent ticket) — approved 2026-09-13. Deterministic
tests for parsing + Mongo persistence (tests 1-6, no LLM). The
"genuine inference, no hardcoded templates" claim itself is proven
separately via `tests/live_repro_phase2_architect_3ideas_2026_09_13.py`
(3 real LLM calls, side-by-side blueprint JSON) per the user's
explicit requirement that one example isn't enough evidence.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.architect_agent import (
    parse_blueprint_json,
    write_blueprint,
    get_blueprint,
    log_architect_event,
)


def test_1_parse_valid_json_passes():
    raw = """{
        "auth": {"needed": false, "type": null, "reasoning": "stateless tool"},
        "data_model": {"entities": [], "reasoning": "no persistence needed"},
        "concurrency": {"applicable": false, "description": null},
        "notifications": {"needed": false, "channels": [], "trigger_events": []},
        "infra": {"cache_or_redis": false, "vector_store_or_rag": false, "background_jobs": false, "reasoning": "n/a"},
        "non_scope": []
    }"""
    bp = parse_blueprint_json(raw)
    assert bp is not None
    assert bp["auth"]["needed"] is False


def test_2_parse_strips_markdown_fences():
    raw = "```json\n{\"auth\":{\"needed\":true,\"type\":\"email\",\"reasoning\":\"x\"},\"data_model\":{\"entities\":[],\"reasoning\":\"x\"},\"concurrency\":{\"applicable\":false,\"description\":null},\"notifications\":{\"needed\":false,\"channels\":[],\"trigger_events\":[]},\"infra\":{\"cache_or_redis\":false,\"vector_store_or_rag\":false,\"background_jobs\":false,\"reasoning\":\"x\"},\"non_scope\":[]}\n```"
    bp = parse_blueprint_json(raw)
    assert bp is not None
    assert bp["auth"]["needed"] is True


def test_3_parse_missing_key_returns_none():
    raw = '{"auth": {"needed": false}}'
    assert parse_blueprint_json(raw) is None


def test_4_parse_garbage_returns_none():
    assert parse_blueprint_json("not json at all") is None
    assert parse_blueprint_json("") is None


@pytest.fixture
def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_5_write_and_read_blueprint_roundtrip(_db):
    pid = f"phase2-test-{uuid_hex()}"
    fake_bp = {
        "auth": {"needed": True, "type": "email", "reasoning": "r"},
        "data_model": {"entities": [{"name": "Order", "fields": ["id"], "persistence": "mongo"}], "reasoning": "r"},
        "concurrency": {"applicable": True, "description": "double-booking"},
        "notifications": {"needed": True, "channels": ["email"], "trigger_events": ["order_placed"]},
        "infra": {"cache_or_redis": False, "vector_store_or_rag": False, "background_jobs": False, "reasoning": "r"},
        "non_scope": [{"item": "payments", "reason": "not asked"}],
    }
    try:
        bp_id = await write_blueprint(
            _db, project_id=pid, user_id="test_admin_001",
            idea="test idea", blueprint=fake_bp,
        )
        assert bp_id
        doc = await get_blueprint(_db, project_id=pid)
        assert doc is not None
        assert doc["blueprint_id"] == bp_id
        assert doc["version"] == 1
        assert doc["blueprint"]["auth"]["needed"] is True

        # Re-write same project — must version up, not duplicate.
        bp_id2 = await write_blueprint(
            _db, project_id=pid, user_id="test_admin_001",
            idea="test idea v2", blueprint=fake_bp,
        )
        doc2 = await get_blueprint(_db, project_id=pid)
        assert doc2["version"] == 2
        assert doc2["blueprint_id"] == bp_id2
        count = await _db.project_blueprints.count_documents({"project_id": pid})
        assert count == 1, "expected exactly ONE current doc per project, got a duplicate"
    finally:
        await _db.project_blueprints.delete_many({"project_id": pid})


@pytest.mark.asyncio
async def test_6_log_architect_event_never_raises_on_bad_db(_db):
    # Passing db=None must be a silent no-op, not a crash.
    await log_architect_event(
        None, event_type="architect_agent_invoked",
        project_id="p", user_id="u", idea="i",
    )
    # Real db — must actually persist.
    await log_architect_event(
        _db, event_type="architect_agent_invoked",
        project_id="phase2-log-test", user_id="test_admin_001", idea="idea text",
    )
    row = await _db.architect_events.find_one(
        {"project_id": "phase2-log-test"}, {"_id": 0},
    )
    assert row is not None
    assert row["event_type"] == "architect_agent_invoked"
    await _db.architect_events.delete_many({"project_id": "phase2-log-test"})


def uuid_hex():
    import uuid
    return uuid.uuid4().hex[:8]
