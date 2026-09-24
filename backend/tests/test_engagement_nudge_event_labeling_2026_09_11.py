"""
tests/test_engagement_nudge_event_labeling_2026_09_11.py — regression
coverage for a real production bug: `emit_engagement_nudge_shown` (and
its `_task_sent` sibling) hardcoded `event_type="post_scan_nudge_shown"`
regardless of the `trigger` argument, so every legitimate "post_skip"
nudge (correctly shown right after a user skips the mandatory repo-
connect gate) got logged under the WRONG funnel event name.

Confirmed against 3 real production users (2026-09-11 founder report):
`repo_connect_gate_skipped -> post_scan_nudge_shown` with zero repo
ever connected and zero scan ever run — the nudge itself was the
correct "post_skip" one, but analytics recorded it as "post_scan",
making the funnel event stream falsely look like the post-scan nudge
fires without a scan.

Fix: the logged event_type now always matches the real trigger —
"post_skip_nudge_shown" / "post_scan_nudge_shown" (and the same split
for "_nudge_task_sent").
"""
import asyncio
import os

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services import signup_guards

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    yield database
    client.close()


async def _latest_event(db, user_id: str) -> dict:
    return await db.funnel_events.find_one(
        {"user_id": user_id}, sort=[("ts_epoch", -1)],
    )


async def test_post_skip_trigger_logs_post_skip_event_not_post_scan(db):
    user_id = "test_regression_post_skip_user"
    await db.funnel_events.delete_many({"user_id": user_id})
    try:
        await signup_guards.emit_engagement_nudge_shown(
            db, user_id=user_id, trigger="post_skip", project_id=None,
        )
        event = await _latest_event(db, user_id)
        assert event is not None
        assert event["event_type"] == "post_skip_nudge_shown", (
            "a post_skip trigger must never be logged as post_scan_nudge_shown"
        )
        assert event["metadata"]["trigger"] == "post_skip"
    finally:
        await db.funnel_events.delete_many({"user_id": user_id})


async def test_post_scan_trigger_still_logs_post_scan_event(db):
    user_id = "test_regression_post_scan_user"
    await db.funnel_events.delete_many({"user_id": user_id})
    try:
        await signup_guards.emit_engagement_nudge_shown(
            db, user_id=user_id, trigger="post_scan", project_id="proj_123",
        )
        event = await _latest_event(db, user_id)
        assert event is not None
        assert event["event_type"] == "post_scan_nudge_shown"
        assert event["metadata"]["trigger"] == "post_scan"
    finally:
        await db.funnel_events.delete_many({"user_id": user_id})


async def test_nudge_task_sent_event_also_matches_trigger(db):
    user_id = "test_regression_task_sent_user"
    await db.funnel_events.delete_many({"user_id": user_id})
    try:
        await signup_guards.emit_engagement_nudge_task_sent(
            db, user_id=user_id, trigger="post_skip", seconds_since_shown=42.0,
        )
        event = await _latest_event(db, user_id)
        assert event is not None
        assert event["event_type"] == "post_skip_nudge_task_sent"
    finally:
        await db.funnel_events.delete_many({"user_id": user_id})
