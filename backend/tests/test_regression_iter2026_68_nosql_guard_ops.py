"""tests/test_regression_iter2026_68_nosql_guard_ops.py

Security audit Phase 4.1: NoSQLOpASGIGuard only denied 5 aggregation-level
operators. $ne / $gt / $regex are the classic login/filter bypass operators
({"password": {"$ne": null}}), so they are now denied at any nesting depth in
user JSON bodies.

Measured before adding (see PR): no frontend code and no route that forwards
a request-body dict into a Mongo query sends these keys, EXCEPT the managed-DB
product (/api/aurem-dev/managed-db/*) whose whole purpose is a client-supplied
Mongo-style filter. That prefix is exempt from the three NEW operators (the
original five stay blocked there). Upload prefixes still bypass the guard
(unchanged; reported as a follow-up). $gte/$lt/$nin/$in are NOT added.
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "ci-test-secret-32-chars-minimum!!")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "aurem_test")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

NEW_OPS = ["$ne", "$gt", "$regex"]
OLD_OPS = ["$where", "$expr", "$function", "$accumulator", "$lookup"]
NOT_ADDED = ["$gte", "$lt", "$nin", "$in", "$eq", "$set"]


# ── unit: the walker ──────────────────────────────────────────────────
@pytest.mark.parametrize("op", NEW_OPS + OLD_OPS)
def test_denied_at_depth_one(op):
    assert main._contains_nosql_op({"password": {op: "x"}}) == op


@pytest.mark.parametrize("op", NEW_OPS)
def test_denied_when_nested_in_lists_and_dicts(op):
    body = {"a": [{"b": {"c": [{"d": {op: 1}}]}}]}
    assert main._contains_nosql_op(body) == op


@pytest.mark.parametrize("op", NEW_OPS)
def test_top_level_key_is_denied(op):
    assert main._contains_nosql_op({op: 1}) == op


@pytest.mark.parametrize("op", NOT_ADDED)
def test_other_operators_are_not_added(op):
    assert main._contains_nosql_op({"x": {op: 1}}) is None


@pytest.mark.parametrize("body", [
    {"email": "a@b.c", "password": "p"}, {"note": "costs $ne 5 and $gt 3"}, {"value": "$regex"},
    {"items": [1, 2, {"name": "x"}]}, [], {}, None, "text", 5, {"a": None}, {"é": {"ü": "$ne"}},
    {"price": "$gt"}, {"list": ["$ne", "$gt"]},
])
def test_legit_payloads_are_unchanged(body):
    assert main._contains_nosql_op(body) is None


def test_values_named_like_operators_are_not_keys():
    assert main._contains_nosql_op({"op": "$ne", "ops": ["$gt", "$regex"]}) is None


def test_depth_cap_still_prevents_cpu_pinning():
    deep: dict = {}
    cur = deep
    for _ in range(5000):
        cur["a"] = {}
        cur = cur["a"]
    cur["$ne"] = 1
    assert main._contains_nosql_op(deep) is None   # beyond depth 12: bounded, never RecursionError


def test_non_string_keys_do_not_crash():
    assert main._contains_nosql_op({1: {"$ne": 1}}) == "$ne"
    assert main._contains_nosql_op({None: [1]}) is None


# ── integration: through the real ASGI guard ──────────────────────────
@pytest.fixture(scope="module")
def client():
    app = FastAPI()

    @app.post("/api/aurem-dev/echo")
    async def echo(body: dict):
        return {"ok": True, "body": body}

    @app.post("/api/aurem-dev/managed-db/app1/notes/find")
    async def managed(body: dict):
        return {"ok": True}

    @app.post("/api/aurem-dev/uploads/x")
    async def upload(body: dict):
        return {"ok": True}

    @app.get("/api/aurem-dev/echo")
    async def get_echo():
        return {"ok": True}

    app.add_middleware(main.NoSQLOpASGIGuard)
    return TestClient(app)


@pytest.mark.parametrize("op", NEW_OPS + OLD_OPS)
@pytest.mark.parametrize("payload", [
    lambda op: {"password": {op: None}},
    lambda op: {"a": [{"b": {"c": {op: 1}}}]},
])
def test_blocked_with_a_generic_400(client, op, payload):
    r = client.post("/api/aurem-dev/echo", json=payload(op))
    assert r.status_code == 400
    assert r.json() == {"detail": "Disallowed query operator in request body"}
    assert op not in r.text            # the operator name is not echoed back


def test_legit_payload_is_passed_through_byte_for_byte(client):
    body = {"email": "a@b.c", "tags": ["x", "y"], "n": 3, "t": "cost $ne"}
    r = client.post("/api/aurem-dev/echo", json=body)
    assert r.status_code == 200 and r.json()["body"] == body


def test_get_and_non_json_requests_are_untouched(client):
    assert client.get("/api/aurem-dev/echo").status_code == 200
    r = client.post("/api/aurem-dev/echo", content='{"$ne": 1}', headers={"content-type": "text/plain"})
    assert r.status_code != 400


def test_managed_db_filters_still_allow_the_new_operators(client):
    r = client.post("/api/aurem-dev/managed-db/app1/notes/find", json={"filter": {"age": {"$gt": 3}, "name": {"$regex": "^a"}}})
    assert r.status_code == 200


@pytest.mark.parametrize("op", OLD_OPS)
def test_managed_db_still_blocks_the_original_five(client, op):
    r = client.post("/api/aurem-dev/managed-db/app1/notes/find", json={"filter": {"x": {op: 1}}})
    assert r.status_code == 400


def test_the_managed_db_exemption_is_prefix_exact(client):
    # an attacker-chosen suffix/lookalike must not inherit the exemption
    app = FastAPI()

    @app.post("/api/aurem-dev/managed-dbx/find")
    async def other(body: dict):
        return {"ok": True}

    app.add_middleware(main.NoSQLOpASGIGuard)
    r = TestClient(app).post("/api/aurem-dev/managed-dbx/find", json={"a": {"$ne": 1}})
    assert r.status_code == 400


def test_upload_prefix_bypass_is_unchanged(client):
    assert client.post("/api/aurem-dev/uploads/x", json={"a": {"$ne": 1}}).status_code == 200


def test_malformed_json_is_not_turned_into_a_guard_error(client):
    r = client.post("/api/aurem-dev/echo", content="{not json", headers={"content-type": "application/json"})
    assert r.status_code != 400 or "Disallowed" not in r.text
