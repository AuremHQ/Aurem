"""tests/test_regression_iter2026_66_harness_telemetry_hardening.py

Hardening of POST /api/harness/telemetry (Fix E, merged in PR #329).

Two review findings, both about real-Mongo behaviour the in-memory
fake of the original suite cannot show:

1. store_events upserts on the unique (user_id, event_hash) index. In
   real Mongo two concurrent upserts for the SAME new key can both
   miss the match and one of them gets DuplicateKeyError. That error
   is a replay (the event is already stored), not a failure — but it
   used to escape as a 500, so the client kept the whole batch queued
   and re-sent it.
2. Invalid events are consumed silently. The client contract needs
   that (otherwise the queue sticks), but a schema drift in a new
   client version would then lose every event with no trace. The
   router now logs HOW MANY events were dropped — a count only, never
   event content, the token or the bin.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

pytestmark = pytest.mark.asyncio

SECRET_TOKEN = "tok_super_secret_value_1234567890"


def _run_event(**over):
    event = {
        "type": "run", "bin": "u1", "harness_version": "0.8.0",
        "model": "qwen3:8b", "task_type": "edit", "passed": True,
        "attempts": 1, "seconds": 2.5, "error_category": "none",
        "at": "2026-11-15T12:00:00Z",
    }
    event.update(over)
    return event


class _Coll:
    """Collection whose update_one fails the way real Mongo does when
    two upserts for the same new key race."""

    def __init__(self, fail_with=None, fail_times=None):
        self.docs = []
        self.fail_with = fail_with
        self.fail_times = fail_times  # None = always
        self.calls = 0

    async def update_one(self, filt, update, upsert=False):
        self.calls += 1
        if self.fail_with is not None and (
            self.fail_times is None or self.calls <= self.fail_times
        ):
            raise self.fail_with
        self.docs.append(dict(filt))


class _DB:
    def __init__(self, coll):
        self.harness_telemetry = coll


# ---------------------------------------------------------------- unit


class TestStoreEventsDuplicateKey:
    async def test_duplicate_key_race_counts_as_replay_not_error(self):
        from services import harness_telemetry as ht
        coll = _Coll(fail_with=DuplicateKeyError("E11000 dup key"))
        events, _ = ht.validate_batch([_run_event()], user_id="u1")
        n = await ht.store_events(_DB(coll), user_id="u1", events=events)
        assert n == 1

    async def test_duplicate_key_on_one_event_does_not_stop_the_rest(self):
        from services import harness_telemetry as ht
        coll = _Coll(fail_with=DuplicateKeyError("dup"), fail_times=1)
        events, _ = ht.validate_batch(
            [_run_event(), _run_event(attempts=2), _run_event(attempts=3)],
            user_id="u1",
        )
        n = await ht.store_events(_DB(coll), user_id="u1", events=events)
        assert n == 3
        assert coll.calls == 3  # the 2nd and 3rd were still attempted
        assert len(coll.docs) == 2

    async def test_other_database_errors_still_propagate(self):
        from services import harness_telemetry as ht
        coll = _Coll(fail_with=RuntimeError("mongo down"))
        events, _ = ht.validate_batch([_run_event()], user_id="u1")
        with pytest.raises(RuntimeError):
            await ht.store_events(_DB(coll), user_id="u1", events=events)

    async def test_empty_event_list_stores_nothing_and_returns_zero(self):
        from services import harness_telemetry as ht
        coll = _Coll()
        assert await ht.store_events(_DB(coll), user_id="u1", events=[]) == 0
        assert coll.calls == 0

    async def test_concurrent_duplicate_key_races_all_succeed(self):
        from services import harness_telemetry as ht
        coll = _Coll(fail_with=DuplicateKeyError("dup"))
        events, _ = ht.validate_batch([_run_event()], user_id="u1")
        results = await asyncio.gather(*[
            ht.store_events(_DB(coll), user_id="u1", events=events)
            for _ in range(5)
        ])
        assert results == [1] * 5


# ----------------------------------------------------------- integration


class _FakeAuthColl:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, filt=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (filt or {}).items()):
                return dict(d)
        return None


class _RouterDB:
    def __init__(self, telemetry):
        self.harness_telemetry = telemetry
        self.harness_tokens = _FakeAuthColl([{
            "token_hash": "H", "user_id": "u1", "revoked_at": None,
        }])
        self.dev_users = _FakeAuthColl([{"user_id": "u1"}])


def _client(db, ht_mod):
    app = FastAPI()
    app.include_router(ht_mod.router, prefix="/api")
    return TestClient(app)


def _patches(ht_mod, db):
    return (
        patch.object(ht_mod, "require_db", lambda: db),
        patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)),
        patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"),
        patch.object(ht_mod, "check_harness_token", AsyncMock(return_value=True)),
    )


def _post(client, events):
    return client.post("/api/harness/telemetry", json={
        "bin": "u1", "token": SECRET_TOKEN, "harness_version": "0.8.0",
        "events": events,
    })


class TestEndpointDuplicateKey:
    async def test_endpoint_returns_200_on_duplicate_key_race(self):
        from routers import harness_telemetry as ht_mod
        db = _RouterDB(_Coll(fail_with=DuplicateKeyError("dup")))
        p1, p2, p3, p4 = _patches(ht_mod, db)
        with p1, p2, p3, p4:
            resp = _post(_client(db, ht_mod), [_run_event(), _run_event(attempts=2)])
        assert resp.status_code == 200
        assert resp.json() == {"received": 2}

    async def test_endpoint_response_never_leaks_the_db_error_text(self):
        from routers import harness_telemetry as ht_mod
        db = _RouterDB(_Coll(fail_with=RuntimeError("mongodb://user:pw@host/db")))
        p1, p2, p3, p4 = _patches(ht_mod, db)
        with p1, p2, p3, p4:
            client = TestClient(_client(db, ht_mod).app, raise_server_exceptions=False)
            resp = _post(client, [_run_event()])
        assert resp.status_code == 500
        assert "mongodb://" not in resp.text and "pw@host" not in resp.text


class TestDroppedEventLogging:
    async def test_dropped_count_is_logged_without_content(self, caplog):
        from routers import harness_telemetry as ht_mod
        db = _RouterDB(_Coll())
        bad = _run_event(model={"$ne": "x"})           # operator injection
        junk = {"type": "run", "prompt": "SECRET-PROMPT-TEXT"}
        p1, p2, p3, p4 = _patches(ht_mod, db)
        with p1, p2, p3, p4, caplog.at_level(logging.INFO, logger="aurem-dev.harness_telemetry"):
            resp = _post(_client(db, ht_mod), [_run_event(), bad, junk])
        assert resp.status_code == 200
        assert resp.json() == {"received": 3}
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "dropped=2" in text
        assert "received=3" in text or "consumed=3" in text
        assert SECRET_TOKEN not in text
        assert "SECRET-PROMPT-TEXT" not in text
        assert "$ne" not in text

    async def test_nothing_is_logged_at_warning_when_all_events_valid(self, caplog):
        from routers import harness_telemetry as ht_mod
        db = _RouterDB(_Coll())
        p1, p2, p3, p4 = _patches(ht_mod, db)
        with p1, p2, p3, p4, caplog.at_level(logging.INFO, logger="aurem-dev.harness_telemetry"):
            resp = _post(_client(db, ht_mod), [_run_event()])
        assert resp.status_code == 200
        assert not any("dropped=" in r.getMessage() for r in caplog.records)

    async def test_log_does_not_contain_the_bin_or_token(self, caplog):
        from routers import harness_telemetry as ht_mod
        db = _RouterDB(_Coll())
        p1, p2, p3, p4 = _patches(ht_mod, db)
        with p1, p2, p3, p4, caplog.at_level(logging.DEBUG, logger="aurem-dev.harness_telemetry"):
            _post(_client(db, ht_mod), [{"type": "nope"}])
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert SECRET_TOKEN not in text
        assert "u1" not in text.replace("dropped=1", "")
