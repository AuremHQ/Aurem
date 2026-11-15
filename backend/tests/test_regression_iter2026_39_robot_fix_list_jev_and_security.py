"""tests/test_regression_iter2026_39_robot_fix_list_jev_and_security.py

Robot Fix List — STEP 4 (Jev observability counters + admin-gated
health endpoint) and remaining security/XSS checks (T4) for the
backend pieces of this task:
  - F5 — stored XSS: a finding title containing `<script>` must come
    back from the API as a literal string (React's default text-node
    escaping handles the render side; this locks the backend never
    does its own "helpful" HTML-ification that would defeat it).
  - Bulk-fix input (B2) real-only gate enforced at the router level,
    not just the helper unit tested in file 37.
  - Admin gate on the new /admin/jev-health endpoint (403 for a
    non-admin caller).
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


def _db():
    from cto_services.db import set_db
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")]
    set_db(db)
    return db


class TestJevObservability:
    def setup_method(self):
        from services.jev_observability import _reset_for_tests
        _reset_for_tests()

    async def test_success_increments_success_counter(self):
        from services.jev_observability import record_jev_call, get_jev_stats
        record_jev_call(ok=True, model="jev-latest")
        stats = get_jev_stats()
        assert stats["success"] == 1
        assert stats["fallback"] == 0
        assert stats["last_model"] == "jev-latest"
        assert stats["last_success_at"] is not None
        assert stats["fallback_rate"] == 0.0

    async def test_failure_increments_fallback_counter_with_error_class(self):
        from services.jev_observability import record_jev_call, get_jev_stats
        record_jev_call(ok=False, error="decisions_status_503: timeout")
        stats = get_jev_stats()
        assert stats["fallback"] == 1
        assert stats["success"] == 0
        assert "503" in stats["last_error_class"]
        assert stats["fallback_rate"] == 1.0

    async def test_no_calls_yet_fallback_rate_is_none_not_zero(self):
        from services.jev_observability import get_jev_stats
        stats = get_jev_stats()
        assert stats["total_calls"] == 0
        assert stats["fallback_rate"] is None

    async def test_semantic_mode_router_jev_failure_still_returns_safe_fallback_and_counts_it(self, monkeypatch):
        """A Jev failure must (1) still produce a usable mode via the
        keyword fallback path (never raise, never block chat) AND
        (2) increment the fallback counter — both halves of STEP 4's
        explicit test ask."""
        from services.jev_observability import _reset_for_tests, get_jev_stats
        _reset_for_tests()

        async def _boom_choice(**kw):
            raise RuntimeError("proxy down")
        monkeypatch.setattr("services.llm.jev_client.ask_choice", _boom_choice)

        import services.semantic_mode_router as smr
        monkeypatch.setattr(smr, "is_enabled", lambda: True)
        monkeypatch.setattr(smr, "_in_canary", lambda sid: True)

        result = await smr.classify_mode_semantic(
            "fix my login bug", None, "proj1", "session-jev-test-1",
        )
        assert result["mode"] in smr._MODES or result["mode"] == "A"
        assert result["source"] in ("keyword_fallback",)
        stats = get_jev_stats()
        assert stats["fallback"] == 1


class TestJevAdminGate:
    async def test_jev_health_requires_admin(self):
        from routers.admin_ops_config import jev_health
        with patch("routers.admin_ops_config._require_admin",
                   side_effect=HTTPException(403, "not_admin")):
            with pytest.raises(HTTPException) as exc:
                await jev_health(authorization="Bearer not-an-admin")
        assert exc.value.status_code == 403

    async def test_jev_health_returns_counters_for_admin(self):
        from routers.admin_ops_config import jev_health
        from services.jev_observability import _reset_for_tests, record_jev_call
        _reset_for_tests()
        record_jev_call(ok=True, model="jev-latest")
        with patch("routers.admin_ops_config._require_admin", return_value={"user_id": "admin1"}):
            result = await jev_health(authorization="Bearer admin-token")
        assert result["ok"] is True
        assert result["success"] == 1


class TestF5XSSPassthrough:
    async def test_script_tag_title_returned_as_literal_string(self):
        """React escapes text nodes by default — this locks the
        BACKEND side of that contract: the title must round-trip
        byte-for-byte as a string (no HTML-decoding/re-encoding that
        could create a different exploitable shape downstream)."""
        from routers.findings import fix_list
        db = _db()
        owner_id = "pytest_f5_owner"
        project_id = f"pytest_f5_proj_{int(time.time())}"
        await db.cto_projects.insert_one({
            "project_id": project_id, "user_id": owner_id,
            "github_owner": "o", "github_repo": "r", "github_branch": "main",
        })
        malicious_title = "<script>alert(document.cookie)</script>"
        try:
            await db.cto_open_findings.insert_one({
                "user_id": owner_id, "project_id": project_id,
                "finding_id": "xss1", "rule_id": "r", "severity": "high",
                "status": "open", "file": "a.py", "line": 1,
                "title": malicious_title, "message": "", "fix_hint": "",
            })
            with patch("routers.findings.current_dev", return_value={"user_id": owner_id}):
                result = await fix_list(project_id=project_id, limit=20, authorization="Bearer x")
            assert result["items"][0]["title"] == malicious_title
            assert isinstance(result["items"][0]["title"], str)
        finally:
            await db.cto_open_findings.delete_many({"project_id": project_id})
            await db.cto_projects.delete_many({"project_id": project_id})


class TestBulkInputRealOnlyGateAtRouter:
    async def test_bulk_endpoint_drops_unverified_finding_before_starting_job(self, monkeypatch):
        from routers import fix_pipeline as fp
        db = _db()
        owner_id = "pytest_bulkgate_owner"
        project_id = f"pytest_bulkgate_proj_{int(time.time())}"
        await db.cto_projects.insert_one({
            "project_id": project_id, "user_id": owner_id,
            "github_owner": "o", "github_repo": "r", "github_branch": "main",
        })
        try:
            await db.cto_open_findings.insert_one({
                "user_id": owner_id, "project_id": project_id,
                "finding_id": "fake-finding-1", "verified": False, "status": "open",
            })

            async def _gate(auth, level):
                return {"user_id": owner_id}, {"user_id": owner_id}
            monkeypatch.setattr(fp, "_gate", _gate)

            with pytest.raises(HTTPException) as exc:
                await fp.start_bulk_fix(
                    {"project_id": project_id,
                     "findings": [{"id": "fake-finding-1", "file": "x.py"}]},
                    authorization="Bearer x",
                )
            assert exc.value.status_code == 400
        finally:
            await db.cto_open_findings.delete_many({"project_id": project_id})
            await db.cto_projects.delete_many({"project_id": project_id})
