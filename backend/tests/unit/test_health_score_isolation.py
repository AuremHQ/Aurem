"""
tests/unit/test_health_score_isolation.py — 2026-11-21

Locks in the get_health_score() isolation fix: ONE failing scorer must
never take down the whole Codebase Health Score page. Before the fix,
get_health_score() awaited the 9 scorers sequentially with no error
isolation, so a single raising scorer turned GET /admin/health-score
into a 500 and the widget showed only an error line — the score never
opened. A failing scorer now degrades to an honest "unscored" for its
own category; the other categories still score, and the overall still
excludes unscored weight (never renormalized).

The 9 scorers are also independent, so they run concurrently — the
concurrency test below locks in that the wall time is NOT the sum of
the scorers (the sequential sum could exceed the widget's 40 s fetch
timeout on a slow pod).
"""
from __future__ import annotations

import asyncio
import time

import pytest

import services.health_score as hs

_ALL_SCORERS = (
    "score_security", "score_bug_density", "score_reliability",
    "score_test_coverage", "score_code_quality", "score_data_handling",
    "score_performance", "score_architecture", "score_devops_infra",
)


def _install_fakes(monkeypatch, *, failing=(), malformed=(), delay_s=0.0):
    """Replace all 9 scorers: fake-scored at 80, except `failing`
    (raise RuntimeError) and `malformed` (return a bad shape)."""
    import services.architecture_health as ah
    monkeypatch.setattr(ah, "run_health_report", lambda: {})

    async def _scored(*a, **kw):
        if delay_s:
            await asyncio.sleep(delay_s)
        return hs._scored(80, {}, "2026-11-21T00:00:00+00:00", live=True)

    async def _raising(*a, **kw):
        if delay_s:
            await asyncio.sleep(delay_s)
        raise RuntimeError("boom — simulated scorer failure")

    async def _bad_shape(*a, **kw):
        return {"status": "weird", "score": 123}

    for name in _ALL_SCORERS:
        if name in failing:
            monkeypatch.setattr(hs, name, _raising)
        elif name in malformed:
            monkeypatch.setattr(hs, name, _bad_shape)
        else:
            monkeypatch.setattr(hs, name, _scored)


@pytest.mark.asyncio
async def test_one_failing_scorer_keeps_the_page_open(monkeypatch):
    """Regression test for the 2026-11-21 'score page does not open'
    report: on the unfixed code this raises RuntimeError out of
    get_health_score() (the endpoint 500s, the widget never shows a
    score); on the fixed code the page opens with 8 categories."""
    _install_fakes(monkeypatch, failing={"score_security"})
    result = await hs.get_health_score(db=None)

    assert result["categories"]["security"]["status"] == "unscored"
    assert "RuntimeError" in result["categories"]["security"]["reason"]
    assert "boom" in result["categories"]["security"]["reason"]
    for name in _ALL_SCORERS:
        if name == "score_security":
            continue
        cat = name.removeprefix("score_")  # scorer fn → category key
        assert result["categories"][cat]["status"] == "scored"
        assert result["categories"][cat]["score"] == 80
    # Overall still computed from the scored weight only (security's 25
    # excluded, never renormalized): 8 categories at 80 → 80.
    assert result["overall_score"] == 80
    assert result["weight_scored_pct"] == 75.0
    assert result["weight_unscored_pct"] == 25.0
    assert result["unscored_categories"] == ["security"]


@pytest.mark.asyncio
async def test_malformed_scorer_result_degrades_to_unscored(monkeypatch):
    _install_fakes(monkeypatch, malformed={"score_devops_infra"})
    result = await hs.get_health_score(db=None)

    assert result["categories"]["devops_infra"]["status"] == "unscored"
    assert "unexpected shape" in result["categories"]["devops_infra"]["reason"]
    assert result["overall_score"] == 80
    assert result["weight_scored_pct"] == 95.0
    assert result["unscored_categories"] == ["devops_infra"]


@pytest.mark.asyncio
async def test_all_scorers_failing_still_returns_a_response(monkeypatch):
    _install_fakes(monkeypatch, failing=set(_ALL_SCORERS))
    result = await hs.get_health_score(db=None)

    assert result["overall_score"] is None
    assert result["weight_scored_pct"] == 0
    assert result["weight_unscored_pct"] == 100.0
    assert result["unscored_categories"] == [
        name.removeprefix("score_") for name in _ALL_SCORERS
    ]
    assert result["identity_header"].startswith("Repo: ")


@pytest.mark.asyncio
async def test_scorers_run_concurrently_not_sequentially(monkeypatch):
    # 9 scorers x 0.5 s each = 4.5 s sequential; concurrent ≈ 0.5 s.
    _install_fakes(monkeypatch, delay_s=0.5)
    started = time.monotonic()
    result = await hs.get_health_score(db=None)
    elapsed = time.monotonic() - started

    assert result["overall_score"] == 80
    assert elapsed < 2.0, f"scorers ran sequentially? took {elapsed:.2f}s"
