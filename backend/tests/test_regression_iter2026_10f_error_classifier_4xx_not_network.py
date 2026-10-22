"""tests/test_regression_iter2026_10f_error_classifier_4xx_not_network.py

Part A fix — ref ORA-5ff229: the user saw a fake "We couldn't reach an
external service just now" when the real cause was a GitHub 422
(Invalid tree entry path, leading slash). Root cause in
services/error_classifier.py::classify_error(): `httpx.HTTPStatusError`
(raised by `r.raise_for_status()` for ANY 4xx/5xx response) lives in
the `httpx` package namespace, same as genuine transport failures
(`httpx.ConnectError`/`httpx.TimeoutException`) — the old
`"httpx" in exc_mod` check matched both, so every GitHub 4xx got the
"network" category/message regardless of its actual status code.

Fix: a real HTTP response (status code + `.response` present) is now
classified by STATUS CODE first — 5xx -> network, 429 -> quota,
401/403 -> auth, 400/404/409/422 -> "input" with a GitHub-specific
message ("GitHub rejected the change.") when the request host
contains "github". Only a response-LESS httpx exception (genuine
timeout/connect failure) still falls through to the module-name check.

Also covers the matching fix in services/cto_projects_helpers.py::_retry
— a non-retryable 4xx (422, not 403/429) must stop retrying
immediately instead of burning all `attempts`.
"""
from __future__ import annotations

import httpx
import pytest

from services.error_classifier import classify_error


def _http_status_error(status: int, host: str = "api.github.com",
                        method: str = "POST", path: str = "/repos/o/r/git/trees",
                        text: str = "") -> httpx.HTTPStatusError:
    req = httpx.Request(method, f"https://{host}{path}")
    resp = httpx.Response(status, request=req, text=text)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return e
    raise AssertionError("status < 400, raise_for_status did not raise")


class TestFourTwoTwoIsNotNetwork:
    def test_422_from_github_is_input_not_network(self):
        exc = _http_status_error(422, text='{"message":"Invalid request..."}')
        result = classify_error(exc)
        assert result["category"] != "network", result
        assert result["category"] == "input"
        assert result["http_status"] == 422
        assert "external service" not in result["user_message"].lower()
        assert result["user_message"] == "GitHub rejected the change."

    def test_404_from_github_is_not_network(self):
        exc = _http_status_error(404)
        result = classify_error(exc)
        assert result["category"] != "network"
        assert result["http_status"] == 404

    def test_409_from_github_is_not_network(self):
        exc = _http_status_error(409)
        result = classify_error(exc)
        assert result["category"] != "network"
        assert result["http_status"] == 409

    def test_4xx_from_a_non_github_host_keeps_the_generic_input_message(self):
        """The GitHub-specific wording must not leak onto unrelated
        4xx errors from other services classified by this same
        app-wide function."""
        exc = _http_status_error(422, host="api.example.com")
        result = classify_error(exc)
        assert result["category"] == "input"
        assert result["user_message"] != "GitHub rejected the change."


class TestRealNetworkFailuresStillNetwork:
    def test_503_from_github_is_network(self):
        exc = _http_status_error(503, text="Service Unavailable")
        result = classify_error(exc)
        assert result["category"] == "network"
        assert result["http_status"] == 503

    def test_connect_timeout_with_no_response_is_network(self):
        exc = httpx.ConnectTimeout("connection timed out")
        result = classify_error(exc)
        assert result["category"] == "network"

    def test_read_timeout_with_no_response_is_network(self):
        exc = httpx.ReadTimeout("read timed out")
        result = classify_error(exc)
        assert result["category"] == "network"


class TestRetryWrapperStopsOnNonRetryable4xx:
    """services/cto_projects_helpers.py::_retry — the OUTER retry
    wrapping gh_api_commit() in worker_api.py/worker_git.py. A 422
    is deterministic; retrying it 4 times just wastes time (and in
    the live incident, re-uploaded every blob 4 times first)."""

    @pytest.mark.asyncio
    async def test_422_stops_after_first_attempt(self):
        from services.cto_projects_helpers import _retry

        call_count = 0

        async def _always_422():
            nonlocal call_count
            call_count += 1
            raise _http_status_error(422, text="Invalid tree entry")

        logged: list[str] = []

        async def _fake_log(task_id, msg, level="info"):
            logged.append(msg)

        import services.cto_projects_helpers as helpers_mod
        orig_log = helpers_mod._log
        helpers_mod._log = _fake_log
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await _retry(_always_422, what="GitHub commit",
                             task_id="t1", attempts=4, base_sleep=0.01)
        finally:
            helpers_mod._log = orig_log

        assert call_count == 1, (
            f"422 must stop after attempt 1, retried {call_count} times"
        )
        assert len(logged) == 1

    @pytest.mark.asyncio
    async def test_429_rate_limit_still_retries(self):
        """429 is explicitly exempted — it's a transient rate limit,
        not a deterministic rejection, so it should still retry."""
        from services.cto_projects_helpers import _retry

        call_count = 0

        async def _fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _http_status_error(429, text="rate limited")
            return "ok"

        import services.cto_projects_helpers as helpers_mod

        async def _fake_log(task_id, msg, level="info"):
            pass
        orig_log = helpers_mod._log
        helpers_mod._log = _fake_log
        try:
            result = await _retry(_fail_then_succeed, what="GitHub commit",
                                   task_id="t1", attempts=4, base_sleep=0.01)
        finally:
            helpers_mod._log = orig_log

        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_503_still_retries_up_to_attempts(self):
        from services.cto_projects_helpers import _retry

        call_count = 0

        async def _always_503():
            nonlocal call_count
            call_count += 1
            raise _http_status_error(503, text="Service Unavailable")

        import services.cto_projects_helpers as helpers_mod

        async def _fake_log(task_id, msg, level="info"):
            pass
        orig_log = helpers_mod._log
        helpers_mod._log = _fake_log
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await _retry(_always_503, what="GitHub commit",
                             task_id="t1", attempts=3, base_sleep=0.01)
        finally:
            helpers_mod._log = orig_log

        assert call_count == 3, f"503 should retry all 3 attempts, got {call_count}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
