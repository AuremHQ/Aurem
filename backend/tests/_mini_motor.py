"""
tests/_mini_motor.py — a small in-memory stand-in for a Motor database.

Only what the chat-reliability tests need, so they run in CI without a
new dependency or a live Mongo. Anything it does not implement raises
NotImplementedError loudly instead of guessing, so a test never passes on
behaviour this fake invented.
"""
from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace


def _get(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set(doc, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        doc = doc.setdefault(part, {})
    doc[parts[-1]] = value


def _op_ok(value, op, arg):
    if op == "$in":
        return value in arg
    if op == "$nin":
        return value not in arg
    if op == "$ne":
        return value != arg
    if op == "$eq":
        return value == arg
    if op == "$exists":
        return (value is not None) == bool(arg)
    cmp = {"$gt": lambda a, b: a > b, "$gte": lambda a, b: a >= b,
           "$lt": lambda a, b: a < b, "$lte": lambda a, b: a <= b}.get(op)
    if cmp is None:
        raise NotImplementedError(f"query operator {op}")
    return value is not None and cmp(value, arg)


def _match(doc, filt):
    for key, cond in (filt or {}).items():
        if key == "$or":
            if not any(_match(doc, f) for f in cond):
                return False
        elif key == "$and":
            if not all(_match(doc, f) for f in cond):
                return False
        elif key.startswith("$"):
            raise NotImplementedError(f"query operator {key}")
        else:
            value = _get(doc, key)
            if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
                if not all(_op_ok(value, op, arg) for op, arg in cond.items()):
                    return False
            elif isinstance(value, list) and not isinstance(cond, list):
                if cond not in value:
                    return False
            elif value != cond:
                return False
    return True


def _project(doc, proj):
    doc = copy.deepcopy(doc)
    if not proj:
        return doc
    slices = {k: v["$slice"] for k, v in proj.items() if isinstance(v, dict) and "$slice" in v}
    plain = {k: v for k, v in proj.items() if k not in slices}
    inclusion = any(v for k, v in plain.items() if k != "_id")
    if inclusion:
        keep = {k for k, v in plain.items() if v} | set(slices)
        out = {k: v for k, v in doc.items() if k in keep}
        if plain.get("_id", 1) and "_id" in doc:
            out["_id"] = doc["_id"]
        doc = out
    else:
        for k, v in plain.items():
            if not v:
                doc.pop(k, None)
    for k, n in slices.items():
        if isinstance(doc.get(k), list):
            doc[k] = doc[k][n:] if n < 0 else doc[k][:n]
    return doc


def _apply(doc, update, inserting=False):
    for op, fields in update.items():
        if op == "$set":
            for k, v in fields.items():
                _set(doc, k, copy.deepcopy(v))
        elif op == "$setOnInsert":
            if inserting:
                for k, v in fields.items():
                    _set(doc, k, copy.deepcopy(v))
        elif op == "$inc":
            for k, v in fields.items():
                _set(doc, k, (_get(doc, k) or 0) + v)
        elif op == "$push":
            for k, v in fields.items():
                lst = _get(doc, k)
                if lst is None:
                    lst = []
                    _set(doc, k, lst)
                lst.extend(copy.deepcopy(v["$each"]) if isinstance(v, dict) and "$each" in v
                           else [copy.deepcopy(v)])
        elif op == "$unset":
            for k in fields:
                doc.pop(k, None)
        else:
            raise NotImplementedError(f"update operator {op}")


class _Cursor:
    def __init__(self, docs, proj):
        self._docs, self._proj = docs, proj

    def sort(self, key, direction=1):
        keys = key if isinstance(key, list) else [(key, direction)]
        for k, d in reversed(keys):
            self._docs.sort(key=lambda x: (_get(x, k) is None, _get(x, k)), reverse=d < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n] if n else self._docs
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    async def to_list(self, length=None):
        docs = self._docs if length is None else self._docs[:length]
        return [_project(d, self._proj) for d in docs]

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield _project(d, self._proj)
        return gen()


class _Collection:
    def __init__(self):
        self._docs: list[dict] = []

    async def insert_one(self, doc):
        doc.setdefault("_id", uuid.uuid4().hex)
        self._docs.append(copy.deepcopy(doc))
        return SimpleNamespace(inserted_id=doc["_id"])

    async def insert_many(self, docs):
        return SimpleNamespace(inserted_ids=[(await self.insert_one(d)).inserted_id for d in docs])

    def find(self, filt=None, projection=None, **_):
        return _Cursor([d for d in self._docs if _match(d, filt)], projection)

    async def find_one(self, filt=None, projection=None, **_):
        for d in self._docs:
            if _match(d, filt):
                return _project(d, projection)
        return None

    async def count_documents(self, filt=None, **_):
        return sum(1 for d in self._docs if _match(d, filt))

    async def update_one(self, filt, update, upsert=False, **_):
        for d in self._docs:
            if _match(d, filt):
                before = copy.deepcopy(d)
                _apply(d, update)
                return SimpleNamespace(matched_count=1, modified_count=int(d != before),
                                       upserted_id=None)
        if not upsert:
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
        new = {k: v for k, v in (filt or {}).items()
               if not k.startswith("$") and not isinstance(v, dict)}
        _apply(new, update, inserting=True)
        return SimpleNamespace(matched_count=0, modified_count=0,
                               upserted_id=(await self.insert_one(new)).inserted_id)

    async def find_one_and_update(self, filt, update, return_document=None, upsert=False,
                                  projection=None, **_):
        from pymongo import ReturnDocument
        for d in self._docs:
            if _match(d, filt):
                before = copy.deepcopy(d)
                _apply(d, update)
                return _project(d if return_document == ReturnDocument.AFTER else before, projection)
        if upsert:
            await self.update_one(filt, update, upsert=True)
            return await self.find_one(filt, projection)
        return None

    async def delete_one(self, filt):
        for i, d in enumerate(self._docs):
            if _match(d, filt):
                del self._docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, filt=None):
        keep = [d for d in self._docs if not _match(d, filt)]
        n, self._docs = len(self._docs) - len(keep), keep
        return SimpleNamespace(deleted_count=n)

    async def create_index(self, *a, **k):
        return "idx"

    def __getattr__(self, name):
        raise NotImplementedError(f"collection method {name}")


class _Database:
    def __init__(self):
        self._cols: dict[str, _Collection] = {}

    def __getitem__(self, name):
        return self._cols.setdefault(name, _Collection())

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


class AsyncMongoMockClient:
    """Drop-in for the one call shape the tests use: `AsyncMongoMockClient()["name"]`."""

    def __init__(self):
        self._dbs: dict[str, _Database] = {}

    def __getitem__(self, name):
        return self._dbs.setdefault(name, _Database())
