"""tests/test_regression_iter2026_74_harness_telemetry.py

Aurem Loop harness — Fix E: build POST /api/harness/telemetry.

The harness client already POSTs batches to
https://auremcto.com/api/harness/telemetry and gets 404 today; events
stay queued (cap 500) and retry forever. This file tests the server
side: auth like verify, strict per-type validation derived from the
client's TELEMETRY_FIELDS (aurem_loop/constants.py:583-597), storage
in `harness_telemetry`, 90-day TTL (ASSUMPTION — founder confirms),
dedupe on (user_id, event hash), per-IP + per-user rate limits.

Contract (client: aurem_loop/telemetry.py `_telemetry_send_batch`):
request {"bin": str, "token": str, "harness_version": str,
"events": [...]} -> 200 {"received": int} = events CONSUMED from the
START of the batch — valid ones stored, invalid ones dropped and
counted. The client requires an int > 0 to clear anything and
clamps to the batch size; it deletes the FIRST N events of the
batch. So consumption is a PREFIX: the server deals with the batch
in order and `received` is how many events from the start it dealt
with. An invalid event is permanent junk (re-sending it would never
make it valid), so it is dropped and counted instead of stopping
the batch — the old "received 0 on a bad first event" is the poison
pill: the client clears nothing and resends the same batch forever.
Event-level problems therefore return 200 with received = consumed
(the invalid events are never stored); only envelope/batch-level
problems return 422/413/401/429.

TDD: this file was written FIRST (red) against stub modules, then
the service + router were implemented (green). See the final report.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------- fake DB
# Mirrors the pattern in test_regression_iter2026_64_..., extended with
# update_one(upsert + $setOnInsert) and create_index, which the
# telemetry service needs (dedupe + TTL index).


class _UpdateResult:
    def __init__(self, matched_count=0, modified_count=0, upserted_id=None):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.created_indexes = []

    def _match(self, d, filt):
        for k, v in (filt or {}).items():
            if isinstance(v, dict):
                return False
            if d.get(k) != v:
                return False
        return True

    async def find_one(self, filt=None, projection=None):
        for d in self.docs:
            if self._match(d, filt):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, filt, update, upsert=False):
        for d in self.docs:
            if self._match(d, filt):
                d.update(update.get("$set", {}))
                return _UpdateResult(matched_count=1, modified_count=1)
        if upsert:
            new_doc = dict(filt)
            new_doc.update(update.get("$setOnInsert", {}))
            new_doc.update(update.get("$set", {}))
            self.docs.append(new_doc)
            return _UpdateResult(matched_count=0, upserted_id=len(self.docs))
        return _UpdateResult(matched_count=0)

    async def update_many(self, filt, update):
        n = 0
        for d in self.docs:
            if self._match(d, filt):
                d.update(update.get("$set", {}))
                n += 1
        return _UpdateResult(matched_count=n, modified_count=n)

    async def create_index(self, keys, **kwargs):
        self.created_indexes.append((keys, kwargs))
        if isinstance(keys, str):
            return f"{keys}_1"
        return "_".join(f"{k}_{v}" for k, v in keys)

    async def count_documents(self, filt=None):
        return sum(1 for d in self.docs if self._match(d, filt))


class _FakeDB:
    def __init__(self):
        self.dev_users = _FakeColl()
        self.harness_tokens = _FakeColl()
        self.harness_telemetry = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


async def _issue_token(db, user_id):
    from services import harness_auth
    # A token is only ever issued to a real user (the download route
    # issues it for a logged-in user), so the user row exists too.
    await db.dev_users.insert_one({"user_id": user_id, "tier": "free"})
    return await harness_auth.issue_harness_token(db, user_id=user_id)


# ------------------------------------------------------------ event builders


def _run_event(**over):
    event = {
        "type": "run", "bin": "u1", "harness_version": "0.8.0",
        "model": "qwen3:8b", "task_type": "fix-error", "passed": True,
        "attempts": 2, "seconds": 5.38, "error_category": "none",
        "at": "2026-11-14T19:00:00Z",
    }
    event.update(over)
    return event


def _loop_c_event(**over):
    event = _run_event(type="loop_c_run", trigger="gate-failed")
    event.update(over)
    return event


def _publish_event(**over):
    event = {
        "type": "version_published", "bin": "u1", "harness_version": "0.8.0",
        "model": "qwen3:8b", "version": 3, "weights_changed_pct": 0.42,
        "gate_gain": 3.5, "canary": True, "at": "2026-11-14T19:00:00Z",
    }
    event.update(over)
    return event


# ------------------------------------------------------- service unit tests


class TestValidateEvent:
    def test_valid_run_loop_c_and_publish_events_are_accepted(self):
        from services import harness_telemetry as ht
        for event in (_run_event(), _loop_c_event(), _publish_event()):
            clean = ht.validate_event(event, user_id="u1")
            assert clean is not None, event
            assert set(clean) <= set(event)

    def test_unknown_type_is_rejected(self):
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(type="nope"), user_id="u1") is None

    def test_missing_type_is_rejected(self):
        from services import harness_telemetry as ht
        event = _run_event()
        del event["type"]
        assert ht.validate_event(event, user_id="u1") is None

    def test_missing_required_field_is_rejected(self):
        from services import harness_telemetry as ht
        for missing in ("passed", "attempts", "seconds", "at", "model"):
            event = _run_event()
            del event[missing]
            assert ht.validate_event(event, user_id="u1") is None, missing

    def test_non_dict_event_is_rejected(self):
        from services import harness_telemetry as ht
        for bad in ("a string", 42, ["run"], None, True):
            assert ht.validate_event(bad, user_id="u1") is None

    def test_mongo_operator_injection_in_a_scalar_field_is_rejected(self):
        """{"$ne": ...} must never pass validation — it is a dict where a
        scalar belongs, and dicts are rejected for every scalar field."""
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(passed={"$ne": True}), user_id="u1") is None
        assert ht.validate_event(_run_event(attempts={"$gt": 0}), user_id="u1") is None
        assert ht.validate_event(_run_event(model={"$ne": None}), user_id="u1") is None
        assert ht.validate_event(_run_event(seconds={"$ne": 1}), user_id="u1") is None

    def test_list_or_nested_dict_in_a_scalar_field_is_rejected(self):
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(model=["qwen3:8b"]), user_id="u1") is None
        assert ht.validate_event(_run_event(task_type={"nested": "dict"}), user_id="u1") is None
        assert ht.validate_event(_run_event(at=["2026-11-14T19:00:00Z"]), user_id="u1") is None

    def test_bool_is_not_an_int_and_int_is_not_a_bool(self):
        """Python's bool is a subclass of int — the validator must tell
        them apart, or `passed: 1` / `attempts: True` would slip through."""
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(passed=1), user_id="u1") is None
        assert ht.validate_event(_run_event(passed=0), user_id="u1") is None
        assert ht.validate_event(_run_event(attempts=True), user_id="u1") is None
        assert ht.validate_event(_run_event(attempts=2.0), user_id="u1") is None
        assert ht.validate_event(_publish_event(version=True), user_id="u1") is None
        assert ht.validate_event(_publish_event(canary=1), user_id="u1") is None

    def test_nan_inf_and_huge_numbers_are_rejected(self):
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(seconds=float("nan")), user_id="u1") is None
        assert ht.validate_event(_run_event(seconds=float("inf")), user_id="u1") is None
        assert ht.validate_event(_run_event(seconds=-float("inf")), user_id="u1") is None
        assert ht.validate_event(_run_event(attempts=10 ** 30), user_id="u1") is None
        assert ht.validate_event(_publish_event(version=10 ** 30), user_id="u1") is None
        assert ht.validate_event(_publish_event(gate_gain=10 ** 30), user_id="u1") is None

    def test_negative_and_zero_bounds(self):
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(attempts=-1), user_id="u1") is None
        assert ht.validate_event(_run_event(seconds=-0.5), user_id="u1") is None
        assert ht.validate_event(_run_event(seconds=-5), user_id="u1") is None
        assert ht.validate_event(_run_event(attempts=0, seconds=0), user_id="u1") is not None
        # int is fine for a float field (JSON 5 == 5.0)
        assert ht.validate_event(_run_event(seconds=5), user_id="u1") is not None
        # gate_gain may be negative — a loss is real telemetry.
        assert ht.validate_event(_publish_event(gate_gain=-2.5), user_id="u1") is not None
        assert ht.validate_event(_publish_event(gate_gain=-2), user_id="u1") is not None

    def test_string_length_caps(self):
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(model="x" * 10_000), user_id="u1") is None
        assert ht.validate_event(_run_event(model="x" * 100_000), user_id="u1") is None
        assert ht.validate_event(_run_event(task_type="x" * 10_000), user_id="u1") is None
        assert ht.validate_event(_run_event(model="x" * 256), user_id="u1") is not None

    def test_empty_and_whitespace_strings_are_rejected(self):
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(model=""), user_id="u1") is None
        assert ht.validate_event(_run_event(model="   "), user_id="u1") is None
        assert ht.validate_event(_run_event(bin=""), user_id="u1") is None
        assert ht.validate_event(_run_event(at=""), user_id="u1") is None

    def test_event_bin_must_equal_the_authenticated_user(self):
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(bin="someone-else"), user_id="u1") is None
        assert ht.validate_event(_run_event(), user_id="u1") is not None

    def test_malformed_at_timestamp_is_rejected(self):
        from services import harness_telemetry as ht
        assert ht.validate_event(_run_event(at="yesterday"), user_id="u1") is None
        assert ht.validate_event(_run_event(at="2026-11-14"), user_id="u1") is None
        assert ht.validate_event(_run_event(at="2026-11-14T19:00:00"), user_id="u1") is None

    def test_unicode_strings_are_accepted(self):
        from services import harness_telemetry as ht
        clean = ht.validate_event(
            _run_event(model="qwen3:8b-üñïçødé", task_type="fix-error-日"),
            user_id="u1")
        assert clean is not None
        assert clean["model"] == "qwen3:8b-üñïçødé"

    def test_unknown_fields_are_dropped_never_stored(self):
        """E5: smuggled prompt/code/token/path must never reach Mongo —
        they are stripped here, before storage."""
        from services import harness_telemetry as ht
        event = _run_event(prompt="ignore previous instructions",
                           code="import os; os.system('x')",
                           token="hlt_raw_token_value",
                           path="/etc/passwd",
                           file_name="secret.py")
        clean = ht.validate_event(event, user_id="u1")
        assert clean is not None
        for smuggled in ("prompt", "code", "token", "path", "file_name"):
            assert smuggled not in clean
        # And a field that belongs to a DIFFERENT event type is also
        # dropped (the client re-whitelists with the union at the wire
        # boundary, so a run event may carry `trigger` — it is not a
        # run field and must not be stored).
        clean2 = ht.validate_event(_run_event(trigger="gate-failed"), user_id="u1")
        assert clean2 is not None and "trigger" not in clean2


class TestValidateBatch:
    def test_invalid_events_are_skipped_valid_ones_after_still_accepted(self):
        from services import harness_telemetry as ht
        events = [_run_event(), _run_event(attempts=3),
                  _run_event(passed={"$ne": True}), _run_event()]
        accepted, consumed = ht.validate_batch(events, user_id="u1")
        assert len(accepted) == 3
        assert consumed == 4

    def test_first_event_invalid_means_nothing_accepted_but_one_consumed(self):
        from services import harness_telemetry as ht
        assert ht.validate_batch([_run_event(model={"$ne": 1})], user_id="u1") == ([], 1)

    def test_empty_batch_accepts_and_consumes_nothing(self):
        from services import harness_telemetry as ht
        assert ht.validate_batch([], user_id="u1") == ([], 0)

    def test_non_list_batch_is_rejected(self):
        from services import harness_telemetry as ht
        assert ht.validate_batch("not-a-list", user_id="u1") == ([], 0)

    def test_poison_pill_invalid_first_event_is_consumed_not_stuck(self):
        """Poison pill (founder-found, reading the router): a batch
        whose FIRST event is invalid must not stick the queue. The
        invalid event is CONSUMED (counted in `consumed`, never
        stored) and the valid events after it are still accepted —
        so the client, which deletes the first `received` events,
        clears the whole batch instead of resending it forever."""
        from services import harness_telemetry as ht
        accepted, consumed = ht.validate_batch(
            [_run_event(passed={"$ne": True}), _run_event(attempts=3)],
            user_id="u1")
        assert len(accepted) == 1
        assert accepted[0]["attempts"] == 3
        assert consumed == 2
        # All-invalid batch: nothing accepted, everything consumed.
        accepted, consumed = ht.validate_batch(
            [_run_event(passed={"$ne": True})], user_id="u1")
        assert accepted == []
        assert consumed == 1


class TestEventHash:
    def test_hash_is_deterministic_and_content_bound(self):
        from services import harness_telemetry as ht
        a = ht.event_hash("u1", _run_event())
        assert a == ht.event_hash("u1", _run_event())
        assert a != ht.event_hash("u1", _run_event(attempts=3))
        assert a != ht.event_hash("u2", _run_event())
        assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)

    def test_hash_does_not_contain_the_token(self):
        from services import harness_telemetry as ht
        assert "hlt_" not in ht.event_hash("u1", _run_event())


class TestStoreEvents:
    async def test_stores_one_doc_per_event_with_the_right_user(self):
        from services import harness_telemetry as ht
        db = _FakeDB()
        events, _consumed = ht.validate_batch([_run_event(), _loop_c_event()], user_id="u1")
        n = await ht.store_events(db, user_id="u1", events=events)
        assert n == 2
        assert len(db.harness_telemetry.docs) == 2
        for doc in db.harness_telemetry.docs:
            assert doc["user_id"] == "u1"
            assert "received_at" in doc
            assert "event_hash" in doc
            assert "token" not in doc
            assert "prompt" not in doc and "code" not in doc

    async def test_replayed_batch_does_not_double_store(self):
        from services import harness_telemetry as ht
        db = _FakeDB()
        events, _consumed = ht.validate_batch([_run_event(), _run_event(attempts=3)], user_id="u1")
        assert await ht.store_events(db, user_id="u1", events=events) == 2
        assert await ht.store_events(db, user_id="u1", events=events) == 2
        assert len(db.harness_telemetry.docs) == 2

    async def test_duplicate_events_inside_one_batch_store_once(self):
        from services import harness_telemetry as ht
        db = _FakeDB()
        events, _consumed = ht.validate_batch([_run_event(), _run_event()], user_id="u1")
        assert await ht.store_events(db, user_id="u1", events=events) == 2
        assert len(db.harness_telemetry.docs) == 1

    async def test_concurrent_identical_batches_do_not_double_count(self):
        """E7: two identical batches racing through asyncio.gather
        must leave exactly N docs (the unique (user_id, event_hash)
        index + upsert makes this atomic in real Mongo)."""
        from services import harness_telemetry as ht
        db = _FakeDB()
        events, _consumed = ht.validate_batch([_run_event(), _loop_c_event()], user_id="u1")
        results = await asyncio.gather(
            ht.store_events(db, user_id="u1", events=events),
            ht.store_events(db, user_id="u1", events=events),
        )
        assert results == [2, 2]
        assert len(db.harness_telemetry.docs) == 2

    async def test_ensure_indexes_creates_ttl_and_unique_dedupe_index(self):
        from services import harness_telemetry as ht
        db = _FakeDB()
        await ht.ensure_indexes(db)
        by_keys = {str(keys): kwargs for keys, kwargs in db.harness_telemetry.created_indexes}
        assert any(kwargs.get("expireAfterSeconds") == 90 * 24 * 3600
                   for kwargs in by_keys.values()), db.harness_telemetry.created_indexes
        assert any(kwargs.get("unique") is True
                   for kwargs in by_keys.values()), db.harness_telemetry.created_indexes


class TestCheckHarnessToken:
    async def test_valid_token_passes(self):
        from services import harness_auth as ha
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        assert await ha.check_harness_token(db, bin="u1", token=token) is True

    async def test_wrong_user_fails(self):
        from services import harness_auth as ha
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        assert await ha.check_harness_token(db, bin="u2", token=token) is False

    async def test_unknown_and_revoked_tokens_fail(self):
        from services import harness_auth as ha
        db = _FakeDB()
        assert await ha.check_harness_token(db, bin="u1", token="hlt_unknown") is False
        token = await _issue_token(db, "u1")
        await ha.revoke_harness_token(db, user_id="u1")
        assert await ha.check_harness_token(db, bin="u1", token=token) is False

    async def test_empty_and_whitespace_identity_fails(self):
        from services import harness_auth as ha
        db = _FakeDB()
        for bin_id, token in (("", "x"), ("u1", ""), ("   ", "x"), ("u1", "   "),
                              (None, "x"), ("u1", None), (123, "x")):
            assert await ha.check_harness_token(db, bin=bin_id, token=token) is False

    async def test_free_tier_identity_may_send(self):
        """Any valid identity — free or paid — may send telemetry.
        Free is not a rejection reason."""
        from services import harness_auth as ha
        db = _FakeDB()
        token = await _issue_token(db, "free-user")
        assert await ha.check_harness_token(db, bin="free-user", token=token) is True


# ------------------------------------------------------ router integration
# E1-E11, driven through the real FastAPI router with TestClient.


def _telemetry_app(db):
    from routers import harness_telemetry as ht_mod
    app = FastAPI()
    app.include_router(ht_mod.router, prefix="/api")
    return app, ht_mod


def _post(client, body=None, content=None, headers=None):
    if content is not None:
        return client.post("/api/harness/telemetry", content=content,
                           headers={"Content-Type": "application/json", **(headers or {})})
    return client.post("/api/harness/telemetry", json=body,
                       headers=headers or {})


class TestTelemetryEndpoint:
    async def test_e1_valid_batch_stores_n_docs_with_the_right_user(self):
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event(), _loop_c_event(),
                                             _publish_event()]})
        assert resp.status_code == 200
        assert resp.json() == {"received": 3}
        assert len(db.harness_telemetry.docs) == 3
        assert all(d["user_id"] == "u1" for d in db.harness_telemetry.docs)

    async def test_e2_bin_of_a_with_token_of_b_is_rejected_idor(self):
        db = _FakeDB()
        token_b = await _issue_token(db, "bob")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            resp = _post(client, {"bin": "alice", "token": token_b,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event(bin="alice")]})
        assert resp.status_code in (401, 403)
        assert resp.json().get("received", 0) == 0
        assert db.harness_telemetry.docs == []

    async def test_e3_unknown_revoked_missing_and_empty_identity_rejected(self):
        from services import harness_auth as ha
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        revoked_token = await _issue_token(db, "u2")
        await ha.revoke_harness_token(db, user_id="u2")
        app, ht_mod = _telemetry_app(db)
        bad_bodies = [
            {"bin": "u1", "token": "hlt_no_such_token", "harness_version": "0.8.0",
             "events": [_run_event()]},
            {"bin": "u2", "token": revoked_token, "harness_version": "0.8.0",
             "events": [_run_event(bin="u2")]},
            {"bin": "u1", "harness_version": "0.8.0", "events": [_run_event()]},
            {"token": token, "harness_version": "0.8.0", "events": [_run_event()]},
            {"bin": "", "token": token, "harness_version": "0.8.0",
             "events": [_run_event()]},
            {"bin": "   ", "token": token, "harness_version": "0.8.0",
             "events": [_run_event()]},
            {"bin": "u1", "token": "", "harness_version": "0.8.0",
             "events": [_run_event()]},
            {"bin": "u1", "token": "   ", "harness_version": "0.8.0",
             "events": [_run_event()]},
        ]
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            for body in bad_bodies:
                resp = _post(client, body)
                assert resp.status_code in (401, 403, 422), body
                assert resp.json().get("received", 0) == 0
        assert db.harness_telemetry.docs == []

    async def test_e4_injection_and_bad_values_are_rejected_or_dropped(self):
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        bad_first_events = [
            _run_event(passed={"$ne": True}),
            _run_event(attempts={"$gt": 0}),
            _run_event(model=["qwen3:8b"]),
            _run_event(seconds=float("nan")),
            _run_event(seconds=float("inf")),
            _run_event(attempts=10 ** 30),
            _run_event(model="x" * 1_000),  # over the 256-char field cap
            _run_event(bin="someone-else"),
            "not-a-dict",
        ]
        # NOTE: an event carrying smuggled extra fields (prompt/code/
        # token/path) is NOT in this list — those fields are DROPPED
        # and the event is still accepted (received 1). That case is
        # covered by test_e5 (nothing smuggled reaches Mongo).
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            # A bad FIRST event -> received 1 (consumed, never
            # stored), so the client clears it instead of resending
            # it forever (the poison pill).
            for bad in bad_first_events:
                resp = _post(client, {"bin": "u1", "token": token,
                                      "harness_version": "0.8.0",
                                      "events": [bad]})
                assert resp.status_code == 200, bad
                assert resp.json() == {"received": 1}, bad
            assert db.harness_telemetry.docs == []
            # A 100 KiB string makes the whole BODY oversized — the
            # size check fires first (413), which is also a rejection:
            # nothing unsafe is stored either way.
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event(model="x" * 100_000)]})
            assert resp.status_code == 413
            assert db.harness_telemetry.docs == []
            # A bad event AFTER a good one -> all three consumed;
            # the two valid ones are stored, the bad one is not.
            # (The two valid events differ — attempts=3 — so the
            # second is a new doc, not a dedupe replay.)
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event(),
                                             _run_event(passed={"$ne": True}),
                                             _run_event(attempts=3)]})
            assert resp.json() == {"received": 3}
            assert len(db.harness_telemetry.docs) == 2

    async def test_e5_smuggled_fields_never_reach_mongo(self):
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event(prompt="p", code="c",
                                                       token="t", path="/p",
                                                       file_name="f.py")]})
        assert resp.json() == {"received": 1}
        stored = json.dumps(db.harness_telemetry.docs, default=str)
        for smuggled in ("prompt", "code", "file_name"):
            assert smuggled not in db.harness_telemetry.docs[0]
        assert "/p" not in stored
        assert db.harness_telemetry.docs[0]["user_id"] == "u1"

    async def test_e6_too_many_events_or_oversized_body_rejected(self):
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            many = [_run_event(attempts=i) for i in range(51)]
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0", "events": many})
            assert resp.status_code in (413, 422)
            assert resp.json().get("received", 0) == 0
            assert db.harness_telemetry.docs == []
            big = b"x" * 70_000
            resp = _post(client, content=big)
            assert resp.status_code == 413
            assert db.harness_telemetry.docs == []
            # A body that DECLARES more than the cap is rejected
            # without the bytes ever being buffered, even when the
            # bytes actually sent are small.
            resp = client.post("/api/harness/telemetry",
                               content=b'{"bin": "u1"}',
                               headers={"Content-Type": "application/json",
                                        "Content-Length": "70000"})
            assert resp.status_code == 413
            assert db.harness_telemetry.docs == []
            # Exactly 50 is allowed.
            fifty = [_run_event(attempts=i) for i in range(50)]
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0", "events": fifty})
            assert resp.json() == {"received": 50}
            assert len(db.harness_telemetry.docs) == 50

    async def test_e7_concurrent_identical_batches_via_router(self):
        import httpx
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                body = {"bin": "u1", "token": token,
                        "harness_version": "0.8.0",
                        "events": [_run_event(), _loop_c_event()]}
                r1, r2 = await asyncio.gather(
                    ac.post("/api/harness/telemetry", json=body),
                    ac.post("/api/harness/telemetry", json=body),
                )
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == {"received": 2} and r2.json() == {"received": 2}
        assert len(db.harness_telemetry.docs) == 2

    async def test_e8_rate_limit_returns_429(self):
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=False)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event()]})
        assert resp.status_code == 429
        assert db.harness_telemetry.docs == []

    async def test_e8_user_limit_after_ip_limit_passes(self):
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        calls = []

        async def _flaky(key, limit):
            calls.append(key)
            return len(calls) == 1  # IP check passes, user check fails

        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", _flaky), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event()]})
        assert resp.status_code == 429
        assert any("harness_telemetry_ip" in k or "ip" in k for k in calls)
        assert any("harness_telemetry" in k and "ip" not in k for k in calls)
        assert db.harness_telemetry.docs == []

    async def test_e9_raw_token_absent_from_db_logs_and_responses(self, caplog):
        import logging
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"), \
             caplog.at_level(logging.DEBUG):
            client = TestClient(app)
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event()]})
        assert resp.status_code == 200
        assert token not in resp.text
        assert token not in json.dumps(db.harness_telemetry.docs, default=str)
        assert token not in json.dumps(db.harness_tokens.docs, default=str)
        for record in caplog.records:
            assert token not in record.getMessage()

    async def test_errors_are_generic_no_echo_no_traceback(self):
        db = _FakeDB()
        await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/harness/telemetry",
                               content=b"{not json",
                               headers={"Content-Type": "application/json"})
            assert resp.status_code in (400, 422)
            assert "not json" not in resp.text
            assert "Traceback" not in resp.text
            resp = _post(client, {"bin": "u1", "token": "hlt_wrong",
                                  "harness_version": "0.8.0",
                                  "events": [_run_event()]})
            assert resp.status_code in (401, 403)
            assert "hlt_wrong" not in resp.text

    async def test_empty_events_batch_returns_received_zero(self):
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0", "events": []})
        assert resp.status_code == 200
        assert resp.json() == {"received": 0}
        assert db.harness_telemetry.docs == []

    async def test_e11_verify_behavior_unchanged(self):
        """Regression: mounting the new telemetry router next to verify
        changes nothing about verify."""
        from routers import harness as h_mod
        from routers import harness_telemetry as ht_mod
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app = FastAPI()
        app.include_router(h_mod.router, prefix="/api")
        app.include_router(ht_mod.router, prefix="/api")
        with patch.object(h_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(h_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            resp = client.post("/api/harness/verify",
                               json={"bin": "u1", "token": token,
                                     "harness_version": "0.8.0"})
            assert resp.status_code == 200
            assert resp.json()["subscriber"] is False  # free tier
            assert resp.json()["plan"] == "free"
            resp = client.post("/api/harness/verify",
                               json={"bin": "u1", "token": "hlt_wrong",
                                     "harness_version": "0.8.0"})
            assert resp.json()["subscriber"] is False
            assert resp.json()["plan"] is None
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event()]})
            assert resp.json() == {"received": 1}

    async def test_e12_poison_pill_first_event_invalid_does_not_stick(self):
        """Poison pill (founder-found, reading the router): a batch
        whose FIRST event is invalid used to return {"received": 0},
        so the client (which clears only when received > 0) resent
        the same batch forever and the queue stuck at cap 500.
        Invalid events are now CONSUMED — counted in `received`,
        never stored — so the client clears the batch and the valid
        events after the bad one are still stored."""
        db = _FakeDB()
        token = await _issue_token(db, "u1")
        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "203.0.113.7"):
            client = TestClient(app)
            # Bad first event (Mongo operator injection), then a valid one.
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event(passed={"$ne": True}),
                                             _run_event(attempts=3)]})
            assert resp.status_code == 200
            assert resp.json() == {"received": 2}
            assert len(db.harness_telemetry.docs) == 1
            assert db.harness_telemetry.docs[0]["attempts"] == 3
            # All-invalid batch: consumed, nothing stored.
            resp = _post(client, {"bin": "u1", "token": token,
                                  "harness_version": "0.8.0",
                                  "events": [_run_event(passed={"$ne": True})]})
            assert resp.status_code == 200
            assert resp.json() == {"received": 1}
            assert len(db.harness_telemetry.docs) == 1


# ------------------------------------------------- E10: real client contract
# Runs only when the aurem-loop client files are attached via
# AUREM_LOOP_CLIENT_DIR (in this sandbox:
# /home/hatch/workspace/aurem-loop-combined). In the ORA repo this test
# skips with a clear reason — the endpoint side is covered by the
# TestClient tests above regardless.


class TestRealClientContract:
    def test_e10_real_telemetry_send_batch_clears_exactly_what_was_accepted(
            self, tmp_path, monkeypatch):
        import threading
        import time

        client_dir = os.environ.get("AUREM_LOOP_CLIENT_DIR")
        if not client_dir or not os.path.isdir(client_dir):
            pytest.skip("aurem-loop client files not attached "
                        "(set AUREM_LOOP_CLIENT_DIR)")
        if client_dir not in sys.path:
            sys.path.insert(0, client_dir)
        try:
            import aurem_loop.paths as client_paths
            import aurem_loop.telemetry as client_telemetry
        except ImportError as exc:  # pragma: no cover - missing client
            pytest.skip(f"aurem-loop client not importable: {exc}")
        uvicorn = pytest.importorskip("uvicorn")

        db = _FakeDB()
        # issue_harness_token is async — run it on a fresh loop.
        token = asyncio.run(_issue_token(db, "u1"))

        home = tmp_path / "harness-home"
        home.mkdir()
        (home / "settings.json").write_text(json.dumps({"telemetry": True}))
        (home / "identity.json").write_text(
            json.dumps({"bin": "u1", "token": token}))
        outbox = home / "telemetry-outbox.jsonl"
        sent = home / "telemetry-sent.jsonl"
        monkeypatch.setattr(client_paths, "SETTINGS_PATH", str(home / "settings.json"))
        monkeypatch.setattr(client_paths, "IDENTITY_PATH", str(home / "identity.json"))
        monkeypatch.setattr(client_paths, "TELEMETRY_OUTBOX_PATH", str(outbox))
        monkeypatch.setattr(client_paths, "TELEMETRY_SENT_PATH", str(sent))

        app, ht_mod = _telemetry_app(db)
        with patch.object(ht_mod, "require_db", lambda: db), \
             patch.object(ht_mod, "check_rate_limit_async", AsyncMock(return_value=True)), \
             patch.object(ht_mod, "client_ip_from_request", lambda req: "127.0.0.1"):
            config = uvicorn.Config(app, host="127.0.0.1", port=0,
                                    log_level="error")
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            deadline = time.time() + 15
            while not server.started and time.time() < deadline:
                time.sleep(0.05)
            assert server.started, "test server did not start"
            port = server.servers[0].sockets[0].getsockname()[1]
            monkeypatch.setenv("AUREM_LOOP_TELEMETRY_URL",
                               f"http://127.0.0.1:{port}/api/harness/telemetry")
            monkeypatch.setenv("AUREM_LOOP_TEST", "1")

            # Two valid events -> client clears exactly 2, server stores 2.
            outbox.write_text(json.dumps(_run_event()) + "\n"
                              + json.dumps(_loop_c_event()) + "\n")
            sent_count, err = client_telemetry._telemetry_send_batch()
            assert (sent_count, err) == (2, None)
            assert outbox.read_text() == ""
            assert len(db.harness_telemetry.docs) == 2

            # Consumed contract: [valid, invalid] -> client clears
            # exactly 2 (both consumed — the invalid one is dropped,
            # never stored, and never resent), outbox empties.
            # (The valid event here differs from the first batch's —
            # attempts=3 — so it is a new doc, not a dedupe replay.)
            outbox.write_text(json.dumps(_run_event(attempts=3)) + "\n"
                              + json.dumps(_run_event(passed={"$ne": True})) + "\n")
            sent_count, err = client_telemetry._telemetry_send_batch()
            assert (sent_count, err) == (2, None)
            assert outbox.read_text() == ""
            assert len(db.harness_telemetry.docs) == 3

            # Auth failure (wrong token in identity) -> client keeps the
            # queue, server stores nothing new.
            (home / "identity.json").write_text(
                json.dumps({"bin": "u1", "token": "hlt_wrong_token"}))
            outbox.write_text(json.dumps(_run_event()) + "\n")
            sent_count, err = client_telemetry._telemetry_send_batch()
            assert sent_count == 0
            assert err == "http-401"
            assert len(outbox.read_text().strip().splitlines()) == 1
            assert len(db.harness_telemetry.docs) == 3

            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
