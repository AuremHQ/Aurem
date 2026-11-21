"""
tests/test_db_restore.py — restore-to-scratch failure cleanup (2026-11-21).

Data Handling fix: a restore that fails MID-ARCHIVE (truncated BSON
stream, bad collection header) used to return early WITHOUT dropping
the partial scratch collection — only the exception handler cleaned
up, and these paths return normally. A corrupt archive therefore
orphaned scratch data until the next drill swept it.

These tests stub the R2 client (local crafted archive file) and the
Motor client (in-memory fake DB) — no live Mongo, no R2 — so they
run anywhere. The real-infra round-trip is covered by
test_db_backup.py (env-gated).
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sys

import bson
import pytest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

pytestmark = pytest.mark.asyncio


# ── fakes ────────────────────────────────────────────────────────
class _FakeCollection:
    def __init__(self, db, name):
        self._db = db
        self._name = name

    async def insert_many(self, docs, ordered=False):
        self._db._created.add(self._name)
        self._db._docs.setdefault(self._name, []).extend(docs)
        return None

    async def count_documents(self, *a, **k):
        return len(self._db._docs.get(self._name, []))


class _FakeDB:
    def __init__(self):
        self._created = set()
        self._docs = {}

    def __getitem__(self, name):
        return _FakeCollection(self, name)

    async def list_collection_names(self):
        return sorted(self._created)

    async def drop_collection(self, name):
        self._created.discard(name)
        self._docs.pop(name, None)

    async def create_collection(self, name):
        self._created.add(name)


class _FakeMotorClient:
    def __init__(self, db):
        self._db = db

    def __getitem__(self, name):
        return self._db

    def close(self):
        pass


class _FakeR2Client:
    """download_file copies a pre-crafted local archive to the target."""

    def __init__(self, archive_path):
        self._archive_path = archive_path

    def download_file(self, bucket, key, path):
        shutil.copyfile(self._archive_path, path)


# ── archive builders ─────────────────────────────────────────────
def _craft_archive(path, collections):
    """Write an aurem-native-v1.gz archive.

    collections: list of (name, [docs...]) — docs are BSON-encoded.
    """
    header = {
        "format": "aurem-native-v1",
        "created_at": "2026-11-21T00:00:00+00:00",
        "source_db": "testdb",
        "total_collections": len(collections),
    }
    with gzip.open(path, "wb", compresslevel=6) as gz:
        gz.write((json.dumps(header) + "\n").encode("utf-8"))
        for name, docs in collections:
            gz.write((json.dumps(
                {"collection": name, "doc_count": len(docs)},
            ) + "\n").encode("utf-8"))
            for doc in docs:
                gz.write(bson.encode(doc))


def _craft_truncated_archive(path):
    """One collection declares 501 docs but only 500 are present —
    the restore hits 'unexpected EOF' on the 501st doc.

    501 (not 2) because inserts flush in batches of 500: with
    fewer docs the truncated read happens BEFORE any flush, so no
    scratch collection exists yet and there is nothing to leak.
    At 500 flushed docs the partial scratch genuinely exists, so
    this test proves the cleanup fix instead of passing vacuously.
    """
    header = {
        "format": "aurem-native-v1",
        "created_at": "2026-11-21T00:00:00+00:00",
        "source_db": "testdb",
        "total_collections": 1,
    }
    docs = [{"_id": i, "v": f"doc-{i}"} for i in range(500)]
    with gzip.open(path, "wb", compresslevel=6) as gz:
        gz.write((json.dumps(header) + "\n").encode("utf-8"))
        gz.write((json.dumps(
            {"collection": "victims", "doc_count": 501},
        ) + "\n").encode("utf-8"))
        for doc in docs:
            gz.write(bson.encode(doc))


def _craft_bad_header_archive(path):
    """First collection restores fine; the second collection header
    line is garbage JSON — 'bad collection header' mid-archive."""
    header = {
        "format": "aurem-native-v1",
        "created_at": "2026-11-21T00:00:00+00:00",
        "source_db": "testdb",
        "total_collections": 2,
    }
    with gzip.open(path, "wb", compresslevel=6) as gz:
        gz.write((json.dumps(header) + "\n").encode("utf-8"))
        gz.write((json.dumps(
            {"collection": "first", "doc_count": 1},
        ) + "\n").encode("utf-8"))
        gz.write(bson.encode({"_id": 1, "v": "x"}))
        gz.write(b"this is not json\n")


# ── tests ────────────────────────────────────────────────────────
async def _run_restore(monkeypatch, tmp_path, archive_path):
    from services import db_restore
    fake_db = _FakeDB()
    monkeypatch.setenv("MONGO_URL", "mongodb://fake:27017")
    monkeypatch.setenv("DB_NAME", "testdb")
    # restore_to_scratch reads R2_BUCKET directly (the client itself
    # is stubbed, so no other R2 env is needed).
    monkeypatch.setenv("R2_BUCKET", "test-bucket")
    monkeypatch.setattr(
        db_restore, "_r2_client",
        lambda: _FakeR2Client(str(archive_path)),
    )
    monkeypatch.setattr(
        db_restore, "AsyncIOMotorClient",
        lambda *a, **k: _FakeMotorClient(fake_db),
    )
    return fake_db, db_restore


async def test_truncated_archive_drops_partial_scratch(monkeypatch, tmp_path):
    """Unexpected EOF mid-collection → ok=False AND no
    `_restore_scratch_*` collection left behind."""
    archive = tmp_path / "truncated.gz"
    _craft_truncated_archive(str(archive))
    fake_db, db_restore = await _run_restore(monkeypatch, tmp_path, archive)

    result = await db_restore.restore_to_scratch(
        r2_key="mongo/aurem_truncated.gz", drop_scratch_after=True,
    )

    assert result["ok"] is False, result
    assert "unexpected EOF" in (result.get("error") or ""), result
    leftovers = [
        n for n in await fake_db.list_collection_names()
        if n.startswith("_restore_scratch_")
    ]
    assert leftovers == [], f"orphaned scratch collections: {leftovers}"


async def test_bad_collection_header_drops_partial_scratch(monkeypatch, tmp_path):
    """Bad collection header after a good collection → ok=False AND
    the first collection's partial scratch is dropped too."""
    archive = tmp_path / "badheader.gz"
    _craft_bad_header_archive(str(archive))
    fake_db, db_restore = await _run_restore(monkeypatch, tmp_path, archive)

    result = await db_restore.restore_to_scratch(
        r2_key="mongo/aurem_badheader.gz", drop_scratch_after=True,
    )

    assert result["ok"] is False, result
    assert "bad collection header" in (result.get("error") or ""), result
    leftovers = [
        n for n in await fake_db.list_collection_names()
        if n.startswith("_restore_scratch_")
    ]
    assert leftovers == [], f"orphaned scratch collections: {leftovers}"


async def test_good_archive_still_restores_and_cleans_up(monkeypatch, tmp_path):
    """Control: a well-formed archive restores ok=True and the
    scratch collection is dropped when drop_scratch_after=True."""
    archive = tmp_path / "good.gz"
    _craft_archive(str(archive), [
        ("alpha", [{"_id": 1}, {"_id": 2}]),
        ("beta", []),
    ])
    fake_db, db_restore = await _run_restore(monkeypatch, tmp_path, archive)

    result = await db_restore.restore_to_scratch(
        r2_key="mongo/aurem_good.gz", drop_scratch_after=True,
    )

    assert result["ok"] is True, result
    assert result["total_docs"] == 2, result
    assert result["total_collections"] == 2, result
    assert result["collection_counts"] == {"alpha": 2, "beta": 0}, result
    leftovers = [
        n for n in await fake_db.list_collection_names()
        if n.startswith("_restore_scratch_")
    ]
    assert leftovers == [], f"scratch not cleaned up: {leftovers}"


async def test_keep_flag_preserves_scratch_on_success(monkeypatch, tmp_path):
    """Control: drop_scratch_after=False keeps the scratch
    collection after a GOOD restore (inspection contract)."""
    archive = tmp_path / "good2.gz"
    _craft_archive(str(archive), [("alpha", [{"_id": 1}])])
    fake_db, db_restore = await _run_restore(monkeypatch, tmp_path, archive)

    result = await db_restore.restore_to_scratch(
        r2_key="mongo/aurem_good2.gz", drop_scratch_after=False,
    )

    assert result["ok"] is True, result
    kept = [
        n for n in await fake_db.list_collection_names()
        if n.startswith("_restore_scratch_")
    ]
    assert len(kept) == 1, f"expected 1 kept scratch collection, got {kept}"
