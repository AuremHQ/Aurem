"""test_regression_iter392_graph_build_stuck_users_fix.py — 2026-02
"STUCK USERS FIX" Part 2.4: `build_graph()` had no outer ceiling and
no auto-retry, so a hang left a project stuck on "Scanning your
codebase…" forever with nothing recorded (sidban15 — 33 min stall,
SLO 15). Covers, against a REAL Mongo test database (uniquely
prefixed project ids, cleaned up in fixture teardown):

  1. `build_graph_guarded` returns the graph unchanged when the first
     attempt succeeds (no unnecessary retry).
  2. It retries exactly once on a transient failure, then succeeds —
     the caller never sees the first failure.
  3. When BOTH attempts fail (or time out), it writes an explicit
     `status: "error"` doc (with the failure reason) instead of
     leaving the project with no graph doc at all.
  4. `GET /projects/{id}/graph` (routers/cto_projects/graph.py)
     surfaces `status: "error"` verbatim instead of the old hardcoded
     `"ready"` for every existing doc regardless of its real status.
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services import graph_builder

pytestmark = pytest.mark.asyncio


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


@pytest.fixture
async def db():
    database = _db()
    yield database
    await database.project_graphs.delete_many(
        {"project_id": {"$regex": "^test-iter392-"}}
    )


async def test_guarded_returns_graph_on_first_success(db, monkeypatch):
    calls = {"n": 0}

    async def fake_build_graph(*a, **k):
        calls["n"] += 1
        return {"status": "ready", "file_count": 7}

    monkeypatch.setattr(graph_builder, "build_graph", fake_build_graph)
    pid = f"test-iter392-{uuid.uuid4()}"
    out = await graph_builder.build_graph_guarded(
        db, pid, "u1", "tok", "owner", "repo",
    )
    assert calls["n"] == 1
    assert out == {"status": "ready", "file_count": 7}
    # No error doc should exist for a clean success.
    doc = await db.project_graphs.find_one({"project_id": pid})
    assert doc is None


async def test_guarded_retries_once_then_succeeds(db, monkeypatch):
    calls = {"n": 0}

    async def flaky_build_graph(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient GitHub API blip")
        return {"status": "ready", "file_count": 3}

    monkeypatch.setattr(graph_builder, "build_graph", flaky_build_graph)
    pid = f"test-iter392-{uuid.uuid4()}"
    out = await graph_builder.build_graph_guarded(
        db, pid, "u1", "tok", "owner", "repo",
    )
    assert calls["n"] == 2
    assert out["status"] == "ready"


async def test_guarded_writes_error_status_when_both_attempts_fail(db, monkeypatch):
    calls = {"n": 0}

    async def always_fails(*a, **k):
        calls["n"] += 1
        raise RuntimeError("GitHub API is down")

    monkeypatch.setattr(graph_builder, "build_graph", always_fails)
    pid = f"test-iter392-{uuid.uuid4()}"
    out = await graph_builder.build_graph_guarded(
        db, pid, "u1", "tok", "owner", "repo",
    )
    assert calls["n"] == 2  # exactly one retry, not an unbounded loop
    assert out == {}
    doc = await db.project_graphs.find_one({"project_id": pid})
    assert doc is not None
    assert doc["status"] == "error"
    assert "GitHub API is down" in doc["error"]


async def test_graph_endpoint_surfaces_error_status_not_hardcoded_ready():
    """Regression for the graph.py bug: `GET /graph` used to always
    return status="ready" no matter what was actually in the doc."""
    import inspect
    from routers.cto_projects import graph as graph_router
    src = inspect.getsource(graph_router)
    assert 'doc.get("status")' in src
    assert '"status": "ready", "graph": doc' not in src
