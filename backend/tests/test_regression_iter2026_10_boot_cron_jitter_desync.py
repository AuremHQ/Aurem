"""tests/test_regression_iter2026_10_boot_cron_jitter_desync.py

Deploy-health bug fix (2026-10): production logs showed repeated
nginx `upstream timed out ... /health` across BOTH pod replicas in
the same ~50s window right as 3 boot crons landed together:
`integration_health_cron` (300-360s), the Chromium self-heal probe
in main.py (330-390s), and `bot_pr_reaper` — which was still a flat
300s sleep with NO jitter, landing squarely inside both of the other
two windows (the exact "multiple heavy boot tasks land in the same
instant" class of bug those two already had jitter for).

This locks in: bot_pr_reaper's initial sleep is jittered and pushed
past both other windows, and the previously-immediate preview
sweeper now also has a boot-settle delay before its first pass.
"""
import re

import pytest


def _read(path: str) -> str:
    with open(path) as fh:
        return fh.read()


def test_bot_pr_reaper_initial_sleep_uses_jitter_past_other_boot_crons():
    src = _read("services/bot_pr_reaper.py")
    # No more flat, un-jittered 300s sleep.
    assert "asyncio.sleep(300)" not in src
    assert "_startup_jitter_s" in src
    m = re.search(r"asyncio\.sleep\((\d+)\s*\+\s*_startup_jitter_s\(\)\)", src)
    assert m, "expected a jittered initial sleep in schedule_bot_pr_reaper_cron"
    base_delay = int(m.group(1))
    # integration_health_cron's window is 300-360s, chromium's is
    # 330-390s (both in main.py / integration_health_cron.py) — the
    # reaper's base delay alone (before its own jitter) must clear
    # the top of both windows so the two ranges can't overlap.
    assert base_delay >= 390


def test_preview_sweeper_cron_has_boot_settle_delay_before_first_pass():
    src = _read("main.py")
    sweeper_def = src[src.index("async def _preview_sweeper_cron"):]
    sweeper_body = sweeper_def[:sweeper_def.index("await _asyncio.sleep(300)")]
    # The first `sleep` call inside the function (before the `while
    # True:` loop body) must not be the recurring 300s interval —
    # there must be a distinct boot-settle delay ahead of the loop.
    assert "await _asyncio.sleep(" in sweeper_body
    assert "_startup_jitter_s" in sweeper_body


@pytest.mark.asyncio
async def test_bot_pr_reaper_cron_schedule_function_still_importable():
    # Confirms the edit didn't break the module (syntax/import-level
    # regression guard for a background-task scheduler with no other
    # direct test coverage).
    from services.bot_pr_reaper import schedule_bot_pr_reaper_cron
    assert callable(schedule_bot_pr_reaper_cron)
