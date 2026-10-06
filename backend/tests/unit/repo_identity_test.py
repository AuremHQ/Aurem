"""
tests/unit/repo_identity_test.py — 2026-09-21 decision-point audit lock-in.

Confirmed via code audit: complexity/health-score source validation is
already fully deterministic — path-scoping (AUREM's own self-scan
never walks `.aurem_cache`) + a live-SHA marker-file check (a customer
repo snapshot is never trusted once its branch has moved) — no LLM
involved, no LLM integration needed. This file locks that behavior in
so a future refactor can't silently break it.

Root cause of the actual mis-attributed-report incident this audit
followed up on: it was NOT a code bug here — it was a human reading a
pasted report with no repo-identity header and assuming it was AUREM's
own code. See services/health_reporter.py's `format_identity_header()`
for that fix.
"""
from __future__ import annotations

import os

import pytest

from services.architecture_health import _default_roots
from services import local_tools as lt
from services.local_tools import _snapshot_cache_key, _ensure_repo_snapshot


# ── AUREM's own self-scan roots never include the cache dir ──────────

def test_aurem_cache_is_never_in_default_roots():
    roots = _default_roots()
    repo_root = os.path.dirname(roots[0])   # roots[0] is .../app/backend
    cache_dir = os.path.join(repo_root, ".aurem_cache")
    for root in roots:
        assert root != cache_dir
        assert not root.startswith(cache_dir + os.sep)
        # And the cache dir must not be reachable by walking DOWN from
        # any default root either (it lives as a sibling, not a child).
        assert not cache_dir.startswith(root + os.sep)


def test_default_roots_are_backend_and_frontend_src_only():
    roots = _default_roots()
    assert len(roots) == 2
    # Location-independent: the repo is /app in the pod but
    # /home/runner/work/<repo>/<repo> on CI.
    assert os.path.basename(roots[0]) == "backend"
    assert roots[1].endswith(os.path.join("frontend", "src"))
    assert os.path.dirname(roots[0]) == os.path.dirname(os.path.dirname(roots[1]))


# ── cache_key() format ────────────────────────────────────────────────

def test_snapshot_cache_key_format():
    assert _snapshot_cache_key("acme-corp", "widgets", "main") == "acme-corp__widgets__main"


def test_snapshot_cache_key_sanitizes_unsafe_characters():
    # Slashes / spaces / colons must never produce a key that could
    # escape _SNAPSHOT_ROOT or collide with another repo's directory.
    key = _snapshot_cache_key("acme/org", "my repo", "feature/x:y")
    assert "/" not in key
    assert " " not in key
    assert ":" not in key
    assert key == "acme_org__my_repo__feature_x_y"


def test_snapshot_cache_key_distinct_repos_never_collide():
    assert _snapshot_cache_key("acme", "widgets", "main") != \
           _snapshot_cache_key("acme", "widgets", "dev")
    assert _snapshot_cache_key("acme", "widgets", "main") != \
           _snapshot_cache_key("other-org", "widgets", "main")


# ── stale-SHA detection actually rejects a mismatched cache ──────────

class _FakeStream:
    def __init__(self, status_code):
        self.status_code = status_code

    async def aiter_bytes(self):
        return
        yield  # pragma: no cover — never reached, keeps this an async generator

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, status_code, calls):
        self._status_code = status_code
        self._calls = calls

    def stream(self, method, url, headers=None):
        self._calls.append((method, url))
        return _FakeStream(self._status_code)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_mismatched_cached_sha_is_rejected_and_triggers_a_real_fetch(monkeypatch, tmp_path):
    """The cached marker says one SHA; the live HEAD is a DIFFERENT
    SHA. A correct implementation must NOT silently trust the stale
    cache — it must attempt a fresh fetch. We prove the fetch was
    genuinely attempted (network call count == 1) rather than
    checking behaviour we can't observe any other way here."""
    monkeypatch.setattr(lt, "_SNAPSHOT_ROOT", str(tmp_path))
    key = _snapshot_cache_key("acme", "widgets", "main")
    dest = tmp_path / key
    dest.mkdir(parents=True)
    (dest / ".aurem_head_sha").write_text("old_sha_0000")

    async def _fake_head_sha(owner, repo, branch, token):
        return "new_sha_1111"   # branch has moved — mismatch

    monkeypatch.setattr(lt, "_repo_head_sha", _fake_head_sha)

    calls = []
    monkeypatch.setattr("services.http.ext_client", lambda *a, **kw: _FakeClient(404, calls))

    snapshot_dir, error = await _ensure_repo_snapshot("acme", "widgets", "main", "tok")

    assert snapshot_dir is None
    assert error == "tarball_status_404"
    assert len(calls) == 1, "a real fetch must have been attempted — stale cache was not trusted"


@pytest.mark.asyncio
async def test_matching_cached_sha_is_a_zero_network_cache_hit(monkeypatch, tmp_path):
    """The cached marker matches the live HEAD exactly — must return
    the cached dir immediately with ZERO tarball network calls."""
    monkeypatch.setattr(lt, "_SNAPSHOT_ROOT", str(tmp_path))
    key = _snapshot_cache_key("acme", "widgets", "main")
    dest = tmp_path / key
    dest.mkdir(parents=True)
    (dest / ".aurem_head_sha").write_text("same_sha_9999")

    async def _fake_head_sha(owner, repo, branch, token):
        return "same_sha_9999"   # matches — cache is fresh

    monkeypatch.setattr(lt, "_repo_head_sha", _fake_head_sha)

    calls = []
    monkeypatch.setattr("services.http.ext_client", lambda *a, **kw: _FakeClient(200, calls))

    snapshot_dir, error = await _ensure_repo_snapshot("acme", "widgets", "main", "tok")

    assert snapshot_dir == str(dest)
    assert error is None
    assert len(calls) == 0, "a matching SHA must be a pure cache hit — no network call at all"
