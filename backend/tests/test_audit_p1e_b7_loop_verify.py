"""test_audit_p1e_b7_loop_verify.py — P1 audit fix B7 (2026-11-25).

B7: verify_files() reported top-level ok=True when a linter binary
was missing (rc=127) — every row honestly carried verified=False,
but a consumer reading only top-level `ok` (the ship-gate) saw a
pass for files that were never checked. Now:
  - an rc=127 row is ok=False (missing binary = environment failure,
    not a pass) and its failure is listed in `errors`;
  - the report carries a top-level `verified` roll-up (True only when
    a real linter ran on EVERY row) and an `unverified` path list;
  - the unmapped-extension skip keeps row ok=True (no linter exists
    for that language at all — deliberately different from rc=127).

No ruff/eslint binary exists on this VM, so the pass path is proved
with a monkeypatched _run returning rc=0 (aggregation behaviour),
and the rc=127 path with a _run that raises FileNotFoundError the
same way asyncio.create_subprocess_exec does for a missing binary.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1e_stubs  # noqa: E402

p1e_stubs.install_p1e_stubs()

from services import loop_verify as lv  # noqa: E402


def _run_missing_binary(monkeypatch):
    """Make _run() take its real FileNotFoundError path: raise the
    same exception create_subprocess_exec raises for a missing
    binary, and let the real _run() translate it to rc=127."""

    async def _fake_exec(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "ruff")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)


def test_b7_missing_ruff_binary_reports_ok_false(monkeypatch):
    """The core bug: one .py file, ruff missing → ok must be False,
    verified False at the top level, path listed unverified."""
    _run_missing_binary(monkeypatch)
    report = asyncio.run(lv.verify_files(
        [{"path": "a.py", "content": "x = 1\n"}]))
    assert report["ok"] is False
    assert report["verified"] is False
    assert report["unverified"] == ["a.py"]
    row = report["results"][0]
    assert row["ok"] is False
    assert row["verified"] is False
    assert row["linter"] == "skip"
    assert row["error_code"] == "FILE_LANGUAGE_UNVERIFIED"
    assert any("a.py" in e and "ruff" in e for e in report["errors"])


def test_b7_all_skipped_batch_is_not_a_pass(monkeypatch):
    """Two files, both linters missing: the old code returned
    ok=True here (all rows ok=True). That is the exact shape the
    ship-gate misread."""
    _run_missing_binary(monkeypatch)
    report = asyncio.run(lv.verify_files([
        {"path": "a.py", "content": "x = 1\n"},
        {"path": "b.ts", "content": "const y = 1;\n"},
    ]))
    assert report["ok"] is False
    assert report["verified"] is False
    assert report["unverified"] == ["a.py", "b.ts"]


def test_b7_unmapped_extension_still_ok_true_but_unverified():
    """Deliberate distinction from rc=127: a .md file has NO linter
    mapping at all, so there is nothing broken to fix — row ok stays
    True (the Loop doesn't over-block), but the top-level verified
    roll-up still says False so no gate can mistake it for checked."""
    report = asyncio.run(lv.verify_files(
        [{"path": "notes.md", "content": "# hi\n"}]))
    assert report["ok"] is True
    assert report["verified"] is False
    assert report["unverified"] == ["notes.md"]
    assert report["results"][0]["linter"] == "skip"
    assert report["results"][0]["verified"] is False


def test_b7_real_lint_pass_still_ok_and_verified(monkeypatch):
    """Pass path unchanged: a linter that runs and exits 0 still
    yields ok=True AND verified=True (proves the new keys don't
    false-negative a genuinely checked batch)."""
    async def _fake_run(cmd, cwd, timeout=8):
        return 0, b"", b""

    monkeypatch.setattr(lv, "_run", _fake_run)
    report = asyncio.run(lv.verify_files(
        [{"path": "a.py", "content": "x = 1\n"}]))
    assert report["ok"] is True
    assert report["verified"] is True
    assert report["unverified"] == []
    assert report["results"][0]["verified"] is True
    assert report["results"][0]["linter"] == "ruff"


def test_b7_mixed_batch_ok_false_unverified_lists_only_skipped(
        monkeypatch):
    """One file genuinely checked, one with a missing binary: ok is
    False, verified is False, and `unverified` names ONLY the
    unchecked file."""
    # Wrap the real _run: the ruff leg returns 0 (checked, passing)
    # while the eslint leg goes through the real spawn — and eslint
    # is not installed on this VM, so it takes the rc=127 path.
    orig_run = lv._run

    async def _mixed_run(cmd, cwd, timeout=8):
        if cmd[0] == "ruff":
            return 0, b"", b""
        return await orig_run(cmd, cwd, timeout)

    monkeypatch.setattr(lv, "_run", _mixed_run)
    report = asyncio.run(lv.verify_files([
        {"path": "a.py", "content": "x = 1\n"},
        {"path": "b.ts", "content": "const y = 1;\n"},
    ]))
    assert report["ok"] is False
    assert report["verified"] is False
    assert report["unverified"] == ["b.ts"]
    by_path = {r["path"]: r for r in report["results"]}
    assert by_path["a.py"]["ok"] is True
    assert by_path["a.py"]["verified"] is True
    assert by_path["b.ts"]["ok"] is False


def test_b7_empty_batch_shape():
    """Empty input keeps ok=True (nothing to check) and carries the
    new keys so consumers can rely on the shape unconditionally."""
    report = asyncio.run(lv.verify_files([]))
    assert report == {"ok": True, "verified": True, "unverified": [],
                      "results": [], "errors": []}


def test_b7_lint_failure_still_ok_false_verified_true(monkeypatch):
    """A real lint FAILURE (rc=1) is unchanged: ok False, but the
    file WAS verified (verified True) — ok and verified are
    independent signals."""
    async def _fake_run(cmd, cwd, timeout=8):
        return 1, b"a.py:1:1: E501 line too long\n", b""

    monkeypatch.setattr(lv, "_run", _fake_run)
    report = asyncio.run(lv.verify_files(
        [{"path": "a.py", "content": "x = 1\n"}]))
    assert report["ok"] is False
    assert report["verified"] is True
    assert report["unverified"] == []
    assert report["results"][0]["verified"] is True
