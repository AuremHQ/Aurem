"""
tests/test_webhook_fence_rate_threshold_2026_09_22.py — fix request:
"GitHub webhook health alert is flapping (red/green repeatedly)".

Root cause (confirmed before this fix): `webhook_fence_status()`'s
`ok` field was `(not missing) and (not failing)` — ANY single failing
delivery in the last-15 window flipped the whole check to red. Since
GitHub's delivery list is a live, shifting window, one stale/transient
failure could sit "in the last 15" for many minutes, then age back
out — flipping ok red->green->red on its own with no real change in
webhook health. Reported real-world observation: 1/15 failing (6.7%)
was consistently tripping red.

Fix: `ok` now requires the FAILURE RATE over the window to exceed
FENCE_FAILURE_RATE_THRESHOLD (0.20 / 20%), not just be non-zero — the
same threshold governs both directions (going red AND recovering),
so there's no asymmetry that makes it easier to flip one way than
the other.

These tests exercise the REAL `webhook_fence_status()` function
(mocked GitHub HTTP layer only) — NOT the higher-level adapter tests
in test_p2f_webhook_fence_alert.py, which mock webhook_fence_status
itself and are unaffected by this change.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body

    @property
    def headers(self):
        return {}


class _FakeClient:
    def __init__(self, subscribed_events, deliveries):
        self._subscribed_events = subscribed_events
        self._deliveries = deliveries

    async def get(self, url, headers=None):
        if url.endswith("/app"):
            return _Resp(200, {"events": self._subscribed_events})
        if "/app/hook/deliveries" in url:
            return _Resp(200, self._deliveries)
        return _Resp(404)


def _deliveries(success_count: int, fail_count: int) -> list:
    """Builds a delivery list — successes first, failures last (order
    doesn't matter to the function under test, it just counts)."""
    out = []
    for i in range(success_count):
        out.append({"id": i, "event": "pull_request", "action": "opened",
                    "delivered_at": "2026-09-22T00:00:00Z", "status_code": 200})
    for i in range(fail_count):
        out.append({"id": 1000 + i, "event": "pull_request", "action": "opened",
                    "delivered_at": "2026-09-22T00:00:00Z", "status_code": 502})
    return out


async def _run_fence(success_count: int, fail_count: int) -> dict:
    fake_client = _FakeClient(
        subscribed_events=["pull_request"],
        deliveries=_deliveries(success_count, fail_count),
    )

    @asynccontextmanager
    async def fake_ext_client(dep, **kw):
        assert dep == "github"
        yield fake_client

    from services import github_app as ga
    with patch.object(ga, "is_configured", lambda: True), \
         patch.object(ga, "ext_client", fake_ext_client), \
         patch.object(ga, "_headers_app", lambda: {"Authorization": "Bearer fake"}):
        return await ga.webhook_fence_status(recent_limit=15)


@pytest.mark.asyncio
async def test_single_blip_1_of_15_does_not_flip_to_red():
    """The exact reported symptom: 1/15 failing (6.7%) — must stay
    green now. Before this fix, `failing` being non-empty alone
    flipped `ok` to False regardless of how small the fraction was."""
    result = await _run_fence(success_count=14, fail_count=1)
    assert result["failing_count"] == 1
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_all_healthy_stays_green():
    result = await _run_fence(success_count=15, fail_count=0)
    assert result["ok"] is True
    assert result["failing_count"] == 0


@pytest.mark.asyncio
async def test_exactly_20_percent_does_not_trip_boundary_is_inclusive_pass():
    """3/15 = exactly 20% — the threshold is '> 20%', so exactly-at
    the boundary must still be treated as healthy (not a real
    degradation, right at the noise floor)."""
    result = await _run_fence(success_count=12, fail_count=3)
    assert result["failing_count"] == 3
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_real_degradation_above_threshold_still_trips_red():
    """4/15 ≈ 26.7% — genuinely above the 20% threshold. This must
    still correctly report red — this fix is about not spamming on
    noise, NOT about hiding a real outage."""
    result = await _run_fence(success_count=11, fail_count=4)
    assert result["failing_count"] == 4
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_fully_broken_pipeline_still_trips_red():
    result = await _run_fence(success_count=0, fail_count=15)
    assert result["ok"] is False
    assert result["failing_count"] == 15


@pytest.mark.asyncio
async def test_recovery_direction_uses_the_same_threshold():
    """Same computation, same threshold, both directions — a real
    outage (5/15 failing, red) recovering down to a single stale
    blip (1/15, still technically 'failing' but under threshold)
    must flip back to green using the identical rule, not a
    different/easier-to-trip rule for one direction."""
    degraded = await _run_fence(success_count=10, fail_count=5)
    assert degraded["ok"] is False

    recovered = await _run_fence(success_count=14, fail_count=1)
    assert recovered["ok"] is True


@pytest.mark.asyncio
async def test_failing_count_and_recent_deliveries_are_never_hidden():
    """The fix must NOT hide real failures from the detail view — only
    the aggregate `ok` verdict changes. `failing_count` and each
    individual delivery's `success` flag must stay exact, even when
    `ok` is True because the rate is under threshold."""
    result = await _run_fence(success_count=14, fail_count=1)
    assert result["failing_count"] == 1
    assert len(result["recent_deliveries"]) == 15
    failures_in_detail = [d for d in result["recent_deliveries"] if not d["success"]]
    assert len(failures_in_detail) == 1


@pytest.mark.asyncio
async def test_empty_window_is_healthy_not_a_divide_by_zero():
    result = await _run_fence(success_count=0, fail_count=0)
    assert result["ok"] is True
    assert result["failing_rate"] == 0.0
