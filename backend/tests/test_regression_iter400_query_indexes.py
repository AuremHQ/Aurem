"""Read-path indexes: created idempotently, never unique, one failure isolated."""
import asyncio

from pymongo.errors import OperationFailure

from services.query_indexes import (
    QUERY_INDEX_SPEC, ensure_query_indexes, get_query_index_report,
)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):
        return list(self._rows)


class _Coll:
    def __init__(self, store, name, fail=None):
        self._store, self._name, self._fail = store, name, fail

    async def create_index(self, keys, **kw):
        if self._fail:
            raise self._fail
        self._store.setdefault(self._name, {})[kw["name"]] = (list(keys), kw)

    def list_indexes(self):
        rows = [{"name": "_id_"}] + [{"name": n} for n in self._store.get(self._name, {})]
        return _Cursor(rows)


class _DB:
    def __init__(self, fail_on=None, fail=None):
        self.store, self._fail_on, self._fail = {}, fail_on, fail

    def __getitem__(self, name):
        return _Coll(self.store, name, self._fail if name == self._fail_on else None)

    async def list_collection_names(self):
        return list({c for c, *_ in QUERY_INDEX_SPEC})


def test_regression_iter400_all_specs_created_and_never_unique():
    db = _DB()
    res = asyncio.run(ensure_query_indexes(db))
    assert len(res) == len(QUERY_INDEX_SPEC) and all(r["ok"] for r in res)
    for coll in db.store.values():
        for _keys, kw in coll.values():
            assert not kw.get("unique")


def test_regression_iter400_spec_names_unique_per_collection():
    seen = {(c, n) for c, _k, n, _s in QUERY_INDEX_SPEC}
    assert len(seen) == len(QUERY_INDEX_SPEC)


def test_regression_iter400_one_failing_collection_does_not_stop_rest():
    db = _DB(fail_on="chat_sessions", fail=RuntimeError("boom"))
    res = asyncio.run(ensure_query_indexes(db))
    bad = [r for r in res if not r["ok"]]
    assert [r["collection"] for r in bad] == ["chat_sessions"]
    assert sum(1 for r in res if r["ok"]) == len(QUERY_INDEX_SPEC) - 1


def test_regression_iter400_existing_index_conflict_is_ok():
    err = OperationFailure("exists", code=85)
    res = asyncio.run(ensure_query_indexes(_DB(fail_on="team_members", fail=err)))
    assert all(r["ok"] for r in res)


def test_regression_iter400_report_lists_missing_then_clean():
    db = _DB()
    before = asyncio.run(get_query_index_report(db))
    assert len(before["missing"]) == len(QUERY_INDEX_SPEC)
    asyncio.run(ensure_query_indexes(db))
    after = asyncio.run(get_query_index_report(db))
    assert after["missing"] == []
