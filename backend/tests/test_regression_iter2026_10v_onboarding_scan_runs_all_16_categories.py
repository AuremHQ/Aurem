"""tests/test_regression_iter2026_10v_onboarding_scan_runs_all_16_categories.py

Closing-the-bypass-doors audit — Step 5b.

`services/project_onboarding_scan.py`'s own module docstring claimed
it "runs the SAME 7-category scanner pipeline" — stale since the
2026-10 "Launch-ready X / 16" expansion merged 9 more categories into
the SAME module-level `SCANNERS` dict this function iterates
(`services.codebase_health_core.SCANNERS.update(EXTRA_SCANNERS)` runs
once at import time, so every consumer of `SCANNERS` — including this
one — already got all 16 without any code change here). Fixed the
comment; this test proves the REAL behavior it describes, not just
the corrected wording.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_onboarding_scan_invokes_all_16_scanner_categories(monkeypatch):
    from services import project_onboarding_scan as pos
    from services.codebase_health_core import SCANNERS

    assert len(SCANNERS) == 16, (
        "sanity check — if this fails, the category count itself "
        "changed and the docstring fix needs re-checking too"
    )

    called_categories: list[str] = []

    def _tracking_scanner(name):
        def _fn(text_cache):
            called_categories.append(name)
            return []
        return _fn

    monkeypatch.setattr(
        "services.repo_text_cache.build_text_cache",
        AsyncMock(return_value={"app.py": "print(1)"}),
    )
    monkeypatch.setattr(
        "services.codebase_health_core.resolve_live_cves", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.codebase_health_core.SCANNERS",
        {name: _tracking_scanner(name) for name in SCANNERS},
    )
    monkeypatch.setattr(
        "services.loop_full_scan.persist_findings_to_backlog",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "services.project_health_trend.record_health_snapshot",
        AsyncMock(return_value=None),
    )

    await pos.run_onboarding_scan(
        db=object(), user_id="u1", project_id="p1",
        github_token="tok", github_owner="o", github_repo="r",
    )

    assert sorted(called_categories) == sorted(SCANNERS.keys())
    assert len(called_categories) == 16, (
        "the docstring now says 16 — the real scan must actually run "
        "all 16, not the stale 7"
    )


def test_docstring_no_longer_claims_7_category():
    from services import project_onboarding_scan as pos
    doc = pos.__doc__ or ""
    assert "7-category" not in doc
    assert "16" in doc


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
