"""tests/test_regression_iter2026_60_part2c_b3_versions_api.py

Part 2c B3 — GET /projects/{project_id}/versions. Flag-gated (404
when `project_versions` is off, which is its default — this task
never turns it on), ownership via finding_scope.resolve_findings_scope
(fail-closed, same as every other project-scoped read in this repo),
free-tier cap at the latest 10 + total/locked_count, paid tiers get
up to `limit` (clamped 1..50), and the response is an explicit
field WHITELIST (no user_id, no raw errors, no token cost).
"""
from __future__ import annotations

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
                return list(self.items)[: (length if length is not None else len(self.items))]
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


def _version_row(*, project_id, n, user_id="u1"):
    return {
        "version_id": f"pv_{n}", "user_id": user_id, "project_id": project_id,
        "n": n, "merged_sha": f"{n:040d}", "pr_url": f"https://x/pull/{n}",
        "pr_number": n, "branch": "b", "source": "ship_pr",
        "summary": f"Change {n}", "merged_at": float(n), "status": "merged",
        "created_at": float(n), "loop_id": None, "batch_id": None,
    }


def _mock_flag(monkeypatch, router_mod, enabled: bool):
    async def _fake_is_enabled(flag, user_id=None, tier=None):
        return enabled
    monkeypatch.setattr(router_mod, "is_enabled", _fake_is_enabled)


def _mock_gate(monkeypatch, router_mod, user_id="u1", tier="pro"):
    async def _fake_gate(authorization, level, db=None):
        caller = {"user_id": user_id, "tier": tier}
        return caller, {"user_id": user_id, "tier": tier}
    monkeypatch.setattr(router_mod, "_gate", _fake_gate)


class TestFlagGating:
    async def test_flag_off_returns_404(self, monkeypatch):
        import routers.project_versions as pv
        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}])
        _mock_gate(monkeypatch, pv)
        _mock_flag(monkeypatch, pv, enabled=False)
        monkeypatch.setattr(pv, "get_db", lambda: db)

        with pytest.raises(HTTPException) as exc:
            await pv.list_project_versions("p1", limit=50, authorization="Bearer x")
        assert exc.value.status_code == 404


class TestOwnership:
    async def test_foreign_project_denied_without_leaking_existence(self, monkeypatch):
        import routers.project_versions as pv
        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "someone_else"}])
        _mock_gate(monkeypatch, pv, user_id="u1")
        _mock_flag(monkeypatch, pv, enabled=True)
        monkeypatch.setattr(pv, "get_db", lambda: db)

        with pytest.raises(HTTPException) as exc:
            await pv.list_project_versions("p1", limit=50, authorization="Bearer x")
        assert exc.value.status_code in (403, 404)

    async def test_nonexistent_project_is_404(self, monkeypatch):
        import routers.project_versions as pv
        db = _FakeDB(projects=[])
        _mock_gate(monkeypatch, pv, user_id="u1")
        _mock_flag(monkeypatch, pv, enabled=True)
        monkeypatch.setattr(pv, "get_db", lambda: db)

        with pytest.raises(HTTPException) as exc:
            await pv.list_project_versions("does_not_exist", limit=50, authorization="Bearer x")
        assert exc.value.status_code == 404

    async def test_mongo_operator_injection_in_project_id_is_inert(self, monkeypatch):
        """project_id is a FastAPI path param typed `str` — a value
        shaped like a Mongo operator arrives as a literal STRING, so
        it can only ever fail to match (never execute as a query
        operator)."""
        import routers.project_versions as pv
        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}])
        _mock_gate(monkeypatch, pv, user_id="u1")
        _mock_flag(monkeypatch, pv, enabled=True)
        monkeypatch.setattr(pv, "get_db", lambda: db)

        with pytest.raises(HTTPException) as exc:
            await pv.list_project_versions('{"$ne": "x"}', limit=50, authorization="Bearer x")
        assert exc.value.status_code == 404


class TestTierGating:
    async def test_free_tier_capped_at_10_with_total_and_locked_count(self, monkeypatch):
        import routers.project_versions as pv
        versions = [_version_row(project_id="p1", n=i) for i in range(1, 16)]  # 15 total
        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}], versions=versions)
        _mock_gate(monkeypatch, pv, user_id="u1", tier="free")
        _mock_flag(monkeypatch, pv, enabled=True)
        monkeypatch.setattr(pv, "get_db", lambda: db)

        result = await pv.list_project_versions("p1", limit=50, authorization="Bearer x")
        assert len(result["versions"]) == 10
        assert result["total"] == 15
        assert result["locked_count"] == 5
        # Newest first.
        assert [v["n"] for v in result["versions"]] == list(range(15, 5, -1))

    async def test_paid_tier_gets_everything_up_to_limit(self, monkeypatch):
        import routers.project_versions as pv
        versions = [_version_row(project_id="p1", n=i) for i in range(1, 16)]
        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}], versions=versions)
        _mock_gate(monkeypatch, pv, user_id="u1", tier="pro")
        _mock_flag(monkeypatch, pv, enabled=True)
        monkeypatch.setattr(pv, "get_db", lambda: db)

        result = await pv.list_project_versions("p1", limit=50, authorization="Bearer x")
        assert len(result["versions"]) == 15
        assert result["total"] == 15
        assert result["locked_count"] == 0

    @pytest.mark.parametrize("bogus_limit,expected", [(0, 1), (-5, 1), (9999, 50), (1000, 50)])
    async def test_limit_is_clamped_1_to_50(self, monkeypatch, bogus_limit, expected):
        import routers.project_versions as pv
        versions = [_version_row(project_id="p1", n=i) for i in range(1, 100)]
        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}], versions=versions)
        _mock_gate(monkeypatch, pv, user_id="u1", tier="team")
        _mock_flag(monkeypatch, pv, enabled=True)
        monkeypatch.setattr(pv, "get_db", lambda: db)

        result = await pv.list_project_versions("p1", limit=bogus_limit, authorization="Bearer x")
        assert len(result["versions"]) == expected


class TestResponseWhitelist:
    async def test_response_never_includes_user_id_or_internal_fields(self, monkeypatch):
        import routers.project_versions as pv
        versions = [_version_row(project_id="p1", n=1)]
        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}], versions=versions)
        _mock_gate(monkeypatch, pv, user_id="u1", tier="pro")
        _mock_flag(monkeypatch, pv, enabled=True)
        monkeypatch.setattr(pv, "get_db", lambda: db)

        result = await pv.list_project_versions("p1", limit=50, authorization="Bearer x")
        row = result["versions"][0]
        for forbidden in ("user_id", "_id", "tokens_charged", "error", "token_cost"):
            assert forbidden not in row

    async def test_empty_project_returns_empty_list_not_an_error(self, monkeypatch):
        import routers.project_versions as pv
        db = _FakeDB(projects=[{"project_id": "p1", "user_id": "u1"}], versions=[])
        _mock_gate(monkeypatch, pv, user_id="u1", tier="pro")
        _mock_flag(monkeypatch, pv, enabled=True)
        monkeypatch.setattr(pv, "get_db", lambda: db)

        result = await pv.list_project_versions("p1", limit=50, authorization="Bearer x")
        assert result["versions"] == []
        assert result["total"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
