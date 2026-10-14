"""
Regression for the reported bug: "ROBO shows the same project issue
over and over, and never removes/updates it once I've fixed it."

Root cause: `persist_findings_to_backlog`'s finding_id computation
assumed EVERY finding carries an explicit `scanner` field. Findings
from codebase_health_core.py / codebase_health_extra.py (the 16
customer-facing scan categories behind "Run all scanners", the
/codebase-health page, the in-chat Fix List panel and the weekly
push-notification strip) never set `scanner` -- they already emit a
complete, globally-unique `id` themselves (e.g.
"sec::auth.py:42:hardcoded_secret"). The old code always re-wrapped
that id as f"{scanner_or_unknown}::{file}:{line}:{rule}", producing a
garbled, double-encoded key that never matched the clean id the
frontend/fix pipeline send back on resolve -- so a fixed finding's
`cto_open_findings` row never flipped to status="fixed" and kept
resurfacing forever.

Fix: `finding_id_for()` now prefers an already-complete `id` when no
`scanner` tag is present, and a new `auto_resolve_stale_findings()`
self-heals the backlog on every scan: any "open" finding in a
category this scan covered, but not re-detected, is closed --
regardless of HOW it got fixed.
"""
import os
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.loop_full_scan import (
    finding_id_for,
    persist_findings_to_backlog,
    auto_resolve_stale_findings,
)


def test_finding_id_for_codebase_health_shape_is_not_double_wrapped():
    """codebase_health_core/extra findings: no `scanner`, `id` already
    complete -- must be used AS-IS, not wrapped again."""
    f = {"id": "sec::auth.py:42:hardcoded_secret", "category": "security",
         "file": "auth.py", "line": 42}
    assert finding_id_for(f) == "sec::auth.py:42:hardcoded_secret"


def test_finding_id_for_tagged_scanner_shape_unchanged():
    """ora_chat_audit / full_scan_orchestrator findings: explicit
    `scanner` tag -- composite-wrap behavior must be unchanged."""
    f = {"scanner": "ora_chat_audit", "id": "hardcoded_secret",
         "file": "x.py", "line": 1}
    assert finding_id_for(f) == "ora_chat_audit::x.py:1:hardcoded_secret"

    f2 = {"scanner": "vanguard", "rule_id": "stripe_live_secret",
          "file": "app/x.py", "line": 10}
    assert finding_id_for(f2) == "vanguard::app/x.py:10:stripe_live_secret"


def test_finding_id_for_no_scanner_no_id_falls_back_safely():
    f = {"rule_id": "http_headers_missing", "file": "main.py", "line": 0}
    assert finding_id_for(f) == "unknown::main.py:0:http_headers_missing"


@pytest.mark.asyncio
async def test_fixed_finding_auto_closes_on_rescan_and_does_not_resurface():
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("MONGO_URL / DB_NAME not set in test env")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    user_id, project_id = "test-user-2026-02-findings-autoresolve", "p_test_2026_02"
    await db.cto_open_findings.delete_many(
        {"user_id": user_id, "project_id": project_id})
    try:
        finding = {"id": "sec::auth.py:42:hardcoded_secret",
                   "category": "security", "severity": "critical",
                   "file": "auth.py", "line": 42, "title": "Hardcoded secret",
                   "message": "stripe key"}
        fid = finding_id_for(finding)

        n = await persist_findings_to_backlog(
            db, user_id=user_id, project_id=project_id,
            findings=[finding], scan_source="codebase_health_manual")
        assert n == 1
        doc = await db.cto_open_findings.find_one(
            {"user_id": user_id, "project_id": project_id})
        assert doc["finding_id"] == fid
        assert doc["status"] == "open"

        # Re-scan the SAME category and it no longer detects the issue
        # (it was fixed, by any means) -- must auto-close.
        closed = await auto_resolve_stale_findings(
            db, user_id=user_id, project_id=project_id,
            categories_scanned=["security"], still_open_ids=set())
        assert closed == 1
        doc2 = await db.cto_open_findings.find_one(
            {"user_id": user_id, "project_id": project_id, "finding_id": fid})
        assert doc2["status"] == "fixed"
        assert doc2["resolved_reason"] == "no_longer_detected"

        # A still-genuinely-present finding in the scanned category must
        # survive the sweep.
        await db.cto_open_findings.update_one(
            {"finding_id": fid}, {"$set": {"status": "open"}})
        still_present = await auto_resolve_stale_findings(
            db, user_id=user_id, project_id=project_id,
            categories_scanned=["security"], still_open_ids={fid})
        assert still_present == 0
        doc3 = await db.cto_open_findings.find_one({"finding_id": fid})
        assert doc3["status"] == "open"

        # A scan of a DIFFERENT category must never touch findings
        # outside its own scope.
        other = {"id": "perf::slow.py:5:n_plus_1", "category": "performance",
                 "severity": "high", "file": "slow.py", "line": 5,
                 "title": "N+1 query", "message": "x"}
        await persist_findings_to_backlog(
            db, user_id=user_id, project_id=project_id,
            findings=[other], scan_source="codebase_health_manual")
        await auto_resolve_stale_findings(
            db, user_id=user_id, project_id=project_id,
            categories_scanned=["security"], still_open_ids=set())
        doc4 = await db.cto_open_findings.find_one(
            {"finding_id": finding_id_for(other)})
        assert doc4["status"] == "open"
    finally:
        await db.cto_open_findings.delete_many(
            {"user_id": user_id, "project_id": project_id})
