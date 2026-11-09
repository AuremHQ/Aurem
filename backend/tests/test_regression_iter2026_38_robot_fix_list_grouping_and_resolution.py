"""tests/test_regression_iter2026_38_robot_fix_list_grouping_and_resolution.py

Robot Fix List — BE1 (severity-sort-before-limit bug) / B3 (grouped
by-file list) / B6 (fallback resolution match + rescan-close).

BE1 root cause: `routers/findings.py::fix_list` sorted the STRING
field `severity` in the Mongo query (alphabetical: critical, high,
info, low, medium) and applied `.limit(20)` BEFORE the real
rank-based python sort ran — with >20 open findings, "low" sorts
before "medium" alphabetically, so mediums got cut from the Mongo
side before python ever got a chance to rank them correctly. Fixed by
fetching all open rows up to a hard cap, THEN rank-sorting, THEN
limiting.

Real local Mongo (MONGO_URL/DB_NAME via tests/conftest.py's .env
loader), same convention as
tests/test_findings_teaser_bridge_2026_08_23.py.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


def _db():
    from cto_services.db import set_db
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")]
    set_db(db)
    return db


async def _seed_project(db, project_id: str, owner_id: str) -> None:
    await db.cto_projects.insert_one({
        "project_id": project_id, "user_id": owner_id,
        "github_owner": "octocat", "github_repo": "Hello-World",
        "github_branch": "main",
    })


async def _seed_finding(db, *, user_id, project_id, finding_id, severity,
                        file="x.py", line=1, rule_id="r", verified=None) -> None:
    doc = {
        "user_id": user_id, "project_id": project_id, "finding_id": finding_id,
        "rule_id": rule_id, "severity": severity, "status": "open",
        "file": file, "line": line, "title": finding_id, "message": "",
        "fix_hint": "", "scanner": "vanguard", "last_seen_at": time.time(),
    }
    if verified is not None:
        doc["verified"] = verified
    await db.cto_open_findings.insert_one(doc)


async def _cleanup(db, project_id: str) -> None:
    await db.cto_open_findings.delete_many({"project_id": project_id})
    await db.cto_projects.delete_many({"project_id": project_id})


class TestBE1SeveritySortBeforeLimit:
    async def test_mediums_not_dropped_for_lows_with_40_open(self):
        """The exact BE1 regression: 40 open findings, limit=20.
        Alphabetically "low" < "medium", so the OLD Mongo-side sort+
        limit would keep lows and drop mediums. The fix must return
        the highest-ranked 20 by REAL severity rank."""
        from routers.findings import fix_list
        db = _db()
        owner_id = "pytest_be1_owner"
        project_id = f"pytest_be1_proj_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            # 15 "low" + 25 "medium" = 40 open. With limit=20, the
            # correct top-20 by severity rank is ALL 25 mediums
            # truncated to 20 (mediums outrank lows), zero lows.
            for i in range(15):
                await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                    finding_id=f"low-{i}", severity="low", file=f"f{i}.py")
            for i in range(25):
                await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                    finding_id=f"med-{i}", severity="medium", file=f"g{i}.py")

            with patch("routers.findings.current_dev", return_value={"user_id": owner_id}):
                result = await fix_list(project_id=project_id, limit=20, authorization="Bearer x")

            severities = [it["severity"] for it in result["items"]]
            assert len(result["items"]) == 20
            assert all(s == "medium" for s in severities), (
                "BE1 regression: a 'low' survived the cut while a 'medium' was dropped"
            )
            assert result["total_open"] == 40
        finally:
            await _cleanup(db, project_id)

    async def test_critical_always_outranks_everything(self):
        from routers.findings import fix_list
        db = _db()
        owner_id = "pytest_be1_owner2"
        project_id = f"pytest_be1_proj2_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            for i in range(20):
                await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                    finding_id=f"med-{i}", severity="medium", file=f"g{i}.py")
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="crit-1", severity="critical", file="z.py")
            with patch("routers.findings.current_dev", return_value={"user_id": owner_id}):
                result = await fix_list(project_id=project_id, limit=5, authorization="Bearer x")
            assert result["items"][0]["severity"] == "critical"
        finally:
            await _cleanup(db, project_id)


class TestB3GroupedByFile:
    async def test_five_findings_two_files_groups_into_two_entries(self):
        from routers.findings import fix_list
        db = _db()
        owner_id = "pytest_b3_owner"
        project_id = f"pytest_b3_proj_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="a1", severity="critical", file="auth.py", line=1)
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="a2", severity="medium", file="auth.py", line=2)
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="a3", severity="low", file="auth.py", line=3)
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="b1", severity="high", file="db.py", line=1)
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="b2", severity="high", file="db.py", line=2)

            with patch("routers.findings.current_dev", return_value={"user_id": owner_id}):
                result = await fix_list(project_id=project_id, limit=20, authorization="Bearer x")

            assert result["total_files"] == 2
            assert result["total_issues"] == 5
            files_by_name = {f["file"]: f for f in result["files"]}
            assert files_by_name["auth.py"]["issue_count"] == 3
            assert files_by_name["auth.py"]["max_severity"] == "critical"
            assert files_by_name["db.py"]["issue_count"] == 2
            # auth.py (critical) must sort before db.py (high)
            assert result["files"][0]["file"] == "auth.py"
            assert "list_version" in result
        finally:
            await _cleanup(db, project_id)

    async def test_unverified_findings_excluded_from_grouped_list(self):
        from routers.findings import fix_list
        db = _db()
        owner_id = "pytest_b3_owner2"
        project_id = f"pytest_b3_proj2_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="real1", severity="high", file="a.py",
                                verified=True)
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="fake1", severity="critical", file="b.py",
                                verified=False)
            with patch("routers.findings.current_dev", return_value={"user_id": owner_id}):
                result = await fix_list(project_id=project_id, limit=20, authorization="Bearer x")
            ids = {it["finding_id"] for it in result["items"]}
            assert ids == {"real1"}
            assert result["total_files"] == 1
        finally:
            await _cleanup(db, project_id)


class TestB6FallbackResolution:
    async def test_fallback_match_closes_finding_with_different_id_format(self):
        """BE6 — id formats differ between scanners and the chat
        audit; the fallback match on (user_id, project_id, file,
        rule_id, line) must close a finding whose primary finding_id
        the caller doesn't know/match."""
        from services.finding_fix_applier import _mark_finding_resolved_if_tracked
        db = _db()
        owner_id = "pytest_b6_owner"
        project_id = f"pytest_b6_proj_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="totally-different-id-format-999",
                                severity="high", file="auth.py", line=42,
                                rule_id="hardcoded_secret")
            await _mark_finding_resolved_if_tracked(
                db, user_id=owner_id, project_id=project_id,
                finding_id="this-id-does-not-match-anything",
                file="auth.py", rule_id="hardcoded_secret", line=42,
            )
            row = await db.cto_open_findings.find_one({"project_id": project_id})
            assert row["status"] == "fixed"
            assert row["resolved_via"] == "fallback_match"
        finally:
            await _cleanup(db, project_id)

    async def test_rescan_close_closes_other_scanner_findings_in_same_file(self):
        """After a fix, re-running the static scanner on the patched
        content and finding another previously-tracked rule no
        longer fires closes THAT finding too — scanner-origin only,
        never a chat-audit row."""
        from services.finding_fix_applier import _mark_finding_resolved_if_tracked
        db = _db()
        owner_id = "pytest_b6_owner2"
        project_id = f"pytest_b6_proj2_{int(time.time())}"
        await _seed_project(db, project_id, owner_id)
        try:
            await _seed_finding(db, user_id=owner_id, project_id=project_id,
                                finding_id="other-rule-id", severity="medium",
                                file="auth.py", line=10, rule_id="weak_hash")
            await db.cto_open_findings.insert_one({
                "user_id": owner_id, "project_id": project_id,
                "finding_id": "chat-row-id", "rule_id": "weak_hash",
                "severity": "medium", "status": "open", "file": "auth.py",
                "line": 10, "scanner": "ora_chat_audit",
            })

            def _scan(text, filepath, **kw):
                return []  # patched content: scanner finds NOTHING now
            import services.vanguard_scanner as vs
            orig = vs.scan_text
            vs.scan_text = _scan
            try:
                await _mark_finding_resolved_if_tracked(
                    db, user_id=owner_id, project_id=project_id,
                    finding_id="unrelated-primary-id",
                    file="auth.py", rule_id="some_other_rule", line=1,
                    patched_content="clean content now",
                )
            finally:
                vs.scan_text = orig

            scanner_row = await db.cto_open_findings.find_one({"finding_id": "other-rule-id"})
            assert scanner_row["status"] == "fixed"
            assert scanner_row["resolved_via"] == "rescan_clean"
            chat_row = await db.cto_open_findings.find_one({"finding_id": "chat-row-id"})
            assert chat_row["status"] == "open", "chat-audit rows must NEVER be rescan-closed"
        finally:
            await _cleanup(db, project_id)

    async def test_never_raises_on_db_error(self, monkeypatch):
        """Write-back failures are non-fatal — a successful fix must
        never be reported as failed just because this bookkeeping
        step broke."""
        from services.finding_fix_applier import _mark_finding_resolved_if_tracked

        class _Boom:
            async def update_one(self, *a, **k):
                raise RuntimeError("boom")

            def find(self, *a, **k):
                raise RuntimeError("boom")

        class _BoomDB:
            cto_open_findings = _Boom()

        await _mark_finding_resolved_if_tracked(
            _BoomDB(), user_id="u", project_id="p", finding_id="f",
            file="a.py", rule_id="r", line=1,
        )  # must not raise
