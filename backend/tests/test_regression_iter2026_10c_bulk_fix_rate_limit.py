"""tests/test_regression_iter2026_10c_bulk_fix_rate_limit.py

Bug report (2026-10): user ran Bulk Fix on ~20 findings. The 1st fix
succeeded, every subsequent fix failed red — GitHub's SECONDARY rate
limit (burst-write abuse detection) tripped partway through the
write-heavy blob+tree+commit+ref sequence, and the GitHub POST calls
in `commit_files` had ZERO retry (unlike the read side, which already
retries on Retry-After). Minutes later, the SAME rate-limited
installation also failed the unrelated connection-status probe, which
classified ANY 403 as a permanent `github_rejected` — feeding straight
into the "CLEAN UP BROKEN PROJECT" delete-prompt for a repo that was
never actually broken, just rate-limited.

Two independent fixes, two test classes:
  1. `commit_files`'s blob/tree/commit POSTs now retry through
     `github_request_with_retry` (honors Retry-After / rate-limit
     reset) instead of raising immediately on the first 403.
  2. `_check_one` now classifies a 403 WITH rate-limit evidence
     (x-ratelimit-remaining=0 or a Retry-After header) as
     `unreachable`, never `disconnected`/`github_rejected` — so it
     can never trigger the delete-prompt banner.
"""
import httpx
import pytest

from routers.repo_status import _check_one


class _FakeAsyncClient:
    """Returns a canned response sequence per URL substring match."""
    def __init__(self, by_url_substr):
        self._by = by_url_substr

    async def get(self, url, headers=None):
        for substr, resp in self._by.items():
            if substr in url:
                return resp
        raise AssertionError(f"unexpected URL in test: {url}")


def _resp(status_code, headers=None):
    r = httpx.Response(status_code, headers=headers or {}, request=httpx.Request("GET", "https://api.github.com/x"))
    return r


class TestCheckOneRateLimitVsRejection:
    @pytest.mark.asyncio
    async def test_403_with_ratelimit_remaining_zero_is_unreachable_not_disconnected(self):
        client = _FakeAsyncClient({
            "/contents/": _resp(403, headers={"x-ratelimit-remaining": "0"}),
        })
        out = await _check_one(client, project_id="p1", owner="o", repo="r",
                                token="tok", auth="pat")
        assert out["status"] == "unreachable"
        assert out["error"] == "github_rate_limited"

    @pytest.mark.asyncio
    async def test_403_with_retry_after_header_is_unreachable_not_disconnected(self):
        """Secondary/burst rate limit — the exact shape GitHub returns
        right after a bulk-fix write burst (remaining can still be >0;
        Retry-After is the real signal)."""
        client = _FakeAsyncClient({
            "/contents/": _resp(403, headers={"retry-after": "30"}),
        })
        out = await _check_one(client, project_id="p1", owner="o", repo="r",
                                token="tok", auth="pat")
        assert out["status"] == "unreachable"
        assert out["error"] == "github_rate_limited"

    @pytest.mark.asyncio
    async def test_plain_403_with_no_rate_limit_evidence_is_still_disconnected(self):
        """A genuinely revoked/expired token (no rate-limit headers at
        all) must still classify as disconnected/github_rejected — the
        fix only carves out the rate-limit case, nothing else."""
        client = _FakeAsyncClient({
            "/contents/": _resp(403, headers={}),
        })
        out = await _check_one(client, project_id="p1", owner="o", repo="r",
                                token="tok", auth="pat")
        assert out["status"] == "disconnected"
        assert out["error"] == "github_rejected"

    @pytest.mark.asyncio
    async def test_404_still_disconnected_repo_not_found(self):
        client = _FakeAsyncClient({"/contents/": _resp(404, headers={})})
        out = await _check_one(client, project_id="p1", owner="o", repo="r",
                                token="tok", auth="pat")
        assert out["status"] == "disconnected"
        assert out["error"] == "repo_not_found"

    @pytest.mark.asyncio
    async def test_200_still_connected(self):
        client = _FakeAsyncClient({"/contents/": _resp(200, headers={})})
        out = await _check_one(client, project_id="p1", owner="o", repo="r",
                                token="tok", auth="pat")
        assert out["status"] == "connected"


class TestCommitFilesWriteRetry:
    def test_blob_tree_commit_posts_use_rate_limit_aware_retry(self):
        """Static-but-behavioural guard: the 3 write POSTs that used to
        be bare `client.post(...) + raise_for_status()` (zero retry)
        now go through `github_request_with_retry`, which is the SAME
        helper already proven (and unit-tested elsewhere) to honor
        GitHub's Retry-After / x-ratelimit-reset. The ref-advance PATCH
        is deliberately NOT included (race-safety, unchanged)."""
        import inspect
        from services import github_api_writer as gaw
        src = inspect.getsource(gaw.commit_files)
        assert "github_request_with_retry" in src
        # The ref-advance PATCH must still be a bare client call —
        # the no-retry safety invariant from Sub-batch 3 is untouched.
        assert 'client.patch(' in src

    @pytest.mark.asyncio
    async def test_github_request_with_retry_recovers_from_one_secondary_rate_limit(self, monkeypatch):
        """End-to-end proof the retry helper itself turns a transient
        burst-limit 403 into an eventual success instead of raising —
        this is the exact mechanism now wrapping the blob/tree/commit
        POSTs."""
        from services.loop_safety import github_request_with_retry

        calls = {"n": 0}

        class _FakeResp:
            def __init__(self, status_code, headers=None):
                self.status_code = status_code
                self.headers = headers or {}

        class _FakeAsyncCtx:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def request(self, method, url, headers=None, json=None, params=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    return _FakeResp(403, headers={"retry-after": "0"})
                return _FakeResp(201, headers={})

        monkeypatch.setattr("services.loop_safety.httpx.AsyncClient",
                             lambda *a, **kw: _FakeAsyncCtx())
        from services.retry_guard import get_breaker
        get_breaker("github").record_success()  # ensure breaker is closed

        r = await github_request_with_retry(
            "POST", "https://api.github.com/repos/o/r/git/blobs",
            headers={}, json={"x": 1}, max_retries=2, timeout=5.0)
        assert r.status_code == 201
        assert calls["n"] == 2  # 1 rate-limited attempt + 1 success
