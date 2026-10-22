"""tests/test_regression_iter2026_10w_admin_paid_tier_count_bugfix.py

Closing-the-bypass-doors audit — Step 5a.

`routers/admin_analytics.py::business_pulse`'s `paid_tiers` list used
to read `["basic", "pro", "maxx", "founder"]` — confusing the chat-
MODE vocabulary ("swift"/"pro"/"maxx", a per-message UI pill) with the
actual `dev_users.tier` vocabulary (free/starter/pro/team/founder,
`services/subscription_tiers.py::Tier`). Since no real user ever has
`tier="basic"` or `tier="maxx"`, this silently excluded every real
"starter" and "team" paying user from the admin paid-count metric.
Fixed to `["starter", "pro", "team", "founder"]` — matching the
already-correct list in `routers/admin_users.py:112` and
`services/wallet.py:465`.

This is a read-side query fix only — no stored `dev_users.tier` value
is touched or renamed.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _FakeDevUsersColl:
    def __init__(self, rows):
        self.rows = rows

    def _match(self, row, query):
        for k, v in query.items():
            if isinstance(v, dict) and "$in" in v:
                if row.get(k) not in v["$in"]:
                    return False
            elif isinstance(v, dict) and ("$exists" in v or "$nin" in v):
                val = row.get(k)
                if "$exists" in v and v["$exists"] and val is None:
                    return False
                if "$nin" in v and val in v["$nin"]:
                    return False
            elif isinstance(v, dict) and "$gte" in v:
                if (row.get(k) or 0) < v["$gte"]:
                    return False
            else:
                if row.get(k) != v:
                    return False
        return True

    async def count_documents(self, query):
        return sum(1 for r in self.rows if self._match(r, query or {}))


class _FakeDB:
    def __init__(self, rows):
        self.dev_users = _FakeDevUsersColl(rows)


_USERS = [
    {"user_id": "u_free",    "tier": "free",    "created_at": 0},
    {"user_id": "u_starter", "tier": "starter",  "created_at": 0},
    {"user_id": "u_pro",     "tier": "pro",      "created_at": 0},
    {"user_id": "u_team",    "tier": "team",     "created_at": 0},
    {"user_id": "u_founder", "tier": "founder",  "created_at": 0},
]


async def test_paid_count_includes_starter_and_team(monkeypatch):
    from routers import admin_analytics as aa

    async def fake_require_admin(auth=None):
        return {"user_id": "admin1", "is_admin": True}
    monkeypatch.setattr(aa, "_require_admin", fake_require_admin)
    fake_db = _FakeDB(list(_USERS))
    monkeypatch.setattr(aa, "require_db", lambda: fake_db)
    monkeypatch.setattr(
        "services.synthetic_filter.synthetic_mongo_filter", dict,
    )

    result = await aa.business_pulse(authorization="Bearer x")
    # starter + pro + team + founder = 4 paid users. free is excluded.
    assert result["raw"]["paid_users"] == 4, (
        "starter and team users must count as paid — the old "
        '["basic","pro","maxx","founder"] filter silently missed them'
    )


async def test_paid_tiers_list_matches_canonical_tier_enum():
    """Direct regression on the literal list itself — the exact bug
    (wrong vocabulary) can't silently come back even if a future edit
    changes business_pulse's structure around it."""
    import ast
    import inspect

    from routers import admin_analytics as aa

    src = inspect.getsource(aa.business_pulse)
    tree = ast.parse(src)
    found = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "paid_tiers"):
            found = ast.literal_eval(node.value)
    assert found is not None, "paid_tiers assignment not found"
    assert set(found) == {"starter", "pro", "team", "founder"}
    assert "basic" not in found
    assert "maxx" not in found


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
