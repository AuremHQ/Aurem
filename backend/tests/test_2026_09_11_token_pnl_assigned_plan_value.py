"""
2026-09-11 — founder-directed fix: Token P&L must show real Stripe
revenue and a separate, clearly-labeled "assigned plan value" (live
Stripe subscription MRR — same figure Cockpit/Financials/Personal
Track show), and the two must NEVER be blended into one number.

Uses a self-contained fake DB (including `customer_chat_cost`, which
the pre-existing test_iter388y fake DB never stubbed — unrelated,
pre-existing gap, not touched here) and mocks
`routers.admin_bi._fetch_stripe_metrics` directly rather than hitting
the real Stripe API.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


class _FakeAggCursor:
    def __init__(self, docs): self._docs = list(docs)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._docs:
            raise StopAsyncIteration
        return self._docs.pop(0)


class _FakeFindCursor:
    def __init__(self, docs): self._docs = list(docs)
    def sort(self, *_a, **_k): return self
    def limit(self, n): self._docs = self._docs[:n]; return self
    async def to_list(self, n): return self._docs[:n]


class _FakeCollection:
    def __init__(self, rows):
        self.rows = list(rows)

    async def count_documents(self, filter_):
        def ok(r):
            for k, v in filter_.items():
                if isinstance(v, dict):
                    if "$gte" in v and r.get(k, 0) < v["$gte"]:
                        return False
                elif r.get(k) != v:
                    return False
            return True
        return sum(1 for r in self.rows if ok(r))

    def aggregate(self, pipeline):
        match = (pipeline[0] or {}).get("$match", {}) if pipeline else {}

        def matches(r):
            for k, v in match.items():
                if isinstance(v, dict):
                    if "$gte" in v and r.get(k, 0) < v["$gte"]:
                        return False
                elif r.get(k) != v:
                    return False
            return True

        filtered = [r for r in self.rows if matches(r)]
        group = next((s.get("$group") for s in pipeline if "$group" in s), {})
        if group.get("_id") == "$model":
            buckets = {}
            for r in filtered:
                m = r.get("model") or "unknown"
                buckets.setdefault(m, {"cost": 0.0, "n": 0})
                buckets[m]["cost"] += float(r.get("cost_usd") or 0)
                buckets[m]["n"] += 1
            return _FakeAggCursor([{"_id": m, "cost": b["cost"], "n": b["n"]}
                                    for m, b in buckets.items()])
        result = {"_id": None, "sum": 0.0, "n": 0}
        for r in filtered:
            result["sum"] += float(r.get("amount") or 0)
            result["n"] += 1
        return _FakeAggCursor([result] if filtered else [])

    def find(self, filter_, projection=None):
        return _FakeFindCursor(list(self.rows))


class _FakeDB:
    def __init__(self, seed):
        self._collections = {
            "cto_payments": _FakeCollection(seed.get("cto_payments", [])),
            "cto_tasks": _FakeCollection(seed.get("cto_tasks", [])),
            "chat_sessions": _FakeCollection(seed.get("chat_sessions", [])),
            "customer_chat_cost": _FakeCollection(seed.get("customer_chat_cost", [])),
        }

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._collections:
            return self._collections[name]
        raise AttributeError(name)


@pytest.mark.asyncio
async def test_token_pnl_keeps_revenue_and_assigned_plan_value_separate(monkeypatch):
    from main import app
    from cto_services.auth import require_admin_dep

    now = 1_800_000_000
    seed = {
        "cto_payments": [
            {"amount": 76.0, "payment_status": "pending",  # NOT paid -> excluded
             "created_at": now - 86400},
        ],
        "customer_chat_cost": [],
    }
    fake_db = _FakeDB(seed)

    async def fake_admin(authorization=None):
        return {"user_id": "admin", "email": "a@x", "is_admin": True}

    async def fake_stripe_metrics():
        return {"mrr_usd": 76.0, "status": "ok", "mode": "test"}

    monkeypatch.setattr("routers.admin_analytics._require_admin", fake_admin)
    monkeypatch.setattr("routers.admin_analytics.require_db", lambda: fake_db)
    monkeypatch.setattr("routers.admin_bi._fetch_stripe_metrics", fake_stripe_metrics)
    monkeypatch.setattr("time.time", lambda: now)
    app.dependency_overrides[require_admin_dep] = lambda: None

    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as ac:
            r = await ac.get("/api/aurem-dev/admin/token-pnl",
                              headers={"Authorization": "Bearer x"})
    finally:
        app.dependency_overrides.pop(require_admin_dep, None)

    assert r.status_code == 200, r.text
    body = r.json()
    # Real revenue: $0 — the only cto_payments row is 'pending', not 'paid'.
    assert body["revenue_month"] == 0.0
    # Assigned plan value: $76 — from live Stripe subscription MRR, a
    # DIFFERENT field, never summed into revenue_month.
    assert body["assigned_plan_value_month"] == 76.0
    assert body["assigned_plan_value_status"] == "ok"
    assert "assigned_plan_value_month" in body["_note"]
    # The two numbers must be independently correct, not blended:
    assert body["revenue_month"] != body["assigned_plan_value_month"]


@pytest.mark.asyncio
async def test_token_pnl_assigned_plan_value_defaults_safely_on_stripe_error(monkeypatch):
    """If the Stripe subscription fetch errors, assigned_plan_value_month
    must fall back to 0 / status=error rather than crashing the endpoint
    or silently mixing in a stale/wrong number."""
    from main import app
    from cto_services.auth import require_admin_dep

    fake_db = _FakeDB({"customer_chat_cost": []})

    async def fake_admin(authorization=None):
        return {"user_id": "admin", "email": "a@x", "is_admin": True}

    async def failing_stripe_metrics():
        raise RuntimeError("Stripe unreachable")

    monkeypatch.setattr("routers.admin_analytics._require_admin", fake_admin)
    monkeypatch.setattr("routers.admin_analytics.require_db", lambda: fake_db)
    monkeypatch.setattr("routers.admin_bi._fetch_stripe_metrics", failing_stripe_metrics)
    app.dependency_overrides[require_admin_dep] = lambda: None

    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as ac:
            r = await ac.get("/api/aurem-dev/admin/token-pnl",
                              headers={"Authorization": "Bearer x"})
    finally:
        app.dependency_overrides.pop(require_admin_dep, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_plan_value_month"] == 0.0
    assert body["assigned_plan_value_status"] == "error"
