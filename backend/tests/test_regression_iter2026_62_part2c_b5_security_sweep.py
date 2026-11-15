"""tests/test_regression_iter2026_62_part2c_b5_security_sweep.py

Part 2c B5 — remaining T4 security cases for Project Versions not
already covered by test_regression_iter2026_56/57/58/59/60/61:
  * `limit` Mongo-operator-injection / type-confusion attempt is
    inert — FastAPI's own `limit: int` type coercion rejects
    anything that isn't a plain integer before the handler ever runs.
  * Cross-tenant IDOR: user A, with their OWN real project, cannot
    read user B's versions by passing B's project_id — same
    resolve_findings_scope fail-closed check, exercised with TWO
    real distinct owners rather than one project with no owner.
  * Webhook signature forgery/replay is NOT re-tested here — it is
    the responsibility of routers/github_app.py's webhook signature
    verification layer (already covered by test_github_app_router.py
    /test_github_app_service.py), which `dispatch_pull_request_
    webhook` explicitly documents running AFTER. A forged/unsigned
    request never reaches `dispatch_pull_request_webhook` at all, so
    it never reaches record_version_on_merge either — reused
    coverage, not duplicated.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []

    async def find_one(self, filt, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                return dict(d)
        return None

    async def count_documents(self, filt):
        return sum(1 for d in self.docs
                   if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)))

    def find(self, filt, proj=None):
        matched = [dict(d) for d in self.docs
                   if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict))]

        class _Cur:
            def __init__(self, items):
                self.items = items

            def sort(self, key, direction):
                self.items.sort(key=lambda x: x.get(key) or 0, reverse=(direction < 0))
                return self

            def limit(self, n):
                self.items = self.items[:n]
                return self

            async def to_list(self, length=None):
                return list(self.items)
        return _Cur(matched)


class _FakeDB:
    def __init__(self, projects=None, versions=None):
        self.cto_projects = _FakeColl(projects or [])
        self.project_versions = _FakeColl(versions or [])
        self.team_members = _FakeColl()

    def __getattr__(self, name):
        return _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


class TestLimitTypeConfusionIsInert:
    def test_limit_param_is_typed_int_rejects_non_integer_at_the_framework_layer(self):
        """FastAPI's own parameter typing is the defense — a value
        like '{"$ne": 1}' or 'DROP TABLE' can never reach the handler
        as anything other than a rejected request (422), since
        `limit: int` is declared on the path function's signature."""
        import routers.project_versions as pv
        sig = inspect.signature(pv.list_project_versions)
        # `from __future__ import annotations` stores this as the
        # string "int" rather than the live type object — still
        # proves FastAPI sees an `int`-typed parameter (it resolves
        # the string via get_type_hints at request-handling time).
        assert sig.parameters["limit"].annotation in (int, "int")


class TestCrossTenantIDOR:
    async def test_user_a_cannot_list_user_bs_versions_via_bs_real_project_id(self, monkeypatch):
        import routers.project_versions as pv

        async def _fake_gate(authorization, level, db=None):
            caller = {"user_id": "userA", "tier": "pro"}
            return caller, {"user_id": "userA", "tier": "pro"}
        monkeypatch.setattr(pv, "_gate", _fake_gate)

        async def _fake_is_enabled(flag, user_id=None, tier=None):
            return True
        monkeypatch.setattr(pv, "is_enabled", _fake_is_enabled)

        db = _FakeDB(
            projects=[{"project_id": "p_B_real", "user_id": "userB"}],
            versions=[{
                "version_id": "pv_1", "user_id": "userB", "project_id": "p_B_real",
                "n": 1, "merged_sha": "a" * 40, "pr_url": "https://x", "pr_number": 1,
                "branch": "b", "source": "ship_pr", "summary": "B's private change",
                "merged_at": 1.0, "status": "merged",
            }],
        )
        monkeypatch.setattr(pv, "get_db", lambda: db)

        with pytest.raises(HTTPException) as exc:
            await pv.list_project_versions("p_B_real", limit=50, authorization="Bearer x")
        assert exc.value.status_code in (403, 404)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
