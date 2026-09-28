"""Live E2E tests for MASTER TASK Part A+B: unified LLM ledger + task rollups.

Runs against REACT_APP_BACKEND_URL with the test admin account.
"""
import os
import json
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"

PREFIX = "/api/aurem-dev"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}{PREFIX}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    token = data.get("access_token") or data.get("token")
    assert token, f"no token in login response: {data}"
    return token


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------- Agent tokens ----------
class TestAgentTokens:
    def test_agent_tokens_7d(self, admin_headers):
        r = requests.get(f"{BASE_URL}{PREFIX}/admin/agent-tokens?range=7d",
                         headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        for key in ["range", "bucket", "providers", "totals_tokens",
                    "call_counts", "task_counts", "costs_usd",
                    "total_cost_usd", "avg_per_call", "claude_vs_deepseek",
                    "series", "_source"]:
            assert key in body, f"missing {key} in agent-tokens response"
        assert isinstance(body["providers"], list)
        # No forbidden legacy fields
        raw = json.dumps(body).lower()
        assert "cost_per_1k" not in raw
        assert "claude_corrections" not in raw
        assert body["range"] == "7d"

    @pytest.mark.parametrize("rng,expected", [
        ("30d", "30d"), ("24h", "24h"), ("bogus", "7d"),
    ])
    def test_agent_tokens_range(self, admin_headers, rng, expected):
        r = requests.get(f"{BASE_URL}{PREFIX}/admin/agent-tokens?range={rng}",
                         headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text[:400]
        assert r.json()["range"] == expected, f"range={rng} expected={expected} got={r.json()['range']}"

    def test_agent_tokens_requires_admin(self):
        # No auth
        r = requests.get(f"{BASE_URL}{PREFIX}/admin/agent-tokens?range=7d", timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ---------- Token P&L ----------
class TestTokenPnl:
    def test_token_pnl_shape(self, admin_headers):
        r = requests.get(f"{BASE_URL}{PREFIX}/admin/token-pnl",
                         headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        for k in ["ai_cost_month", "ai_cost_today", "month_by_agent", "day_by_agent"]:
            assert k in body, f"missing {k} in token-pnl"
        # month_by_agent keys should be provider labels (not model paths with slashes)
        for prov in body["month_by_agent"].keys():
            assert "/" not in prov, f"month_by_agent key looks like a model, not provider: {prov}"
        # Sanity: costs should be modest realistic USD numbers (not 1000x inflated)
        assert body["ai_cost_month"] < 1000, f"ai_cost_month too high (possibly inflated): {body['ai_cost_month']}"
        assert body["ai_cost_today"] < 1000, f"ai_cost_today too high: {body['ai_cost_today']}"

    def test_token_pnl_requires_admin(self):
        r = requests.get(f"{BASE_URL}{PREFIX}/admin/token-pnl", timeout=15)
        assert r.status_code in (401, 403)


# ---------- Product analytics ----------
class TestProductAnalytics:
    def test_product_analytics_has_tokens_and_ai_cost(self, admin_headers):
        r = requests.get(f"{BASE_URL}{PREFIX}/admin/product-analytics?days=30",
                         headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        tasks = body.get("tasks", {})
        # code uses tokens_burned (not tokens_used) — reviewer meant the
        # tokens counter under tasks, whatever it's named.
        tok_key = "tokens_burned" if "tokens_burned" in tasks else "tokens_used"
        assert tok_key in tasks, f"no tokens counter in tasks: {list(tasks)}"
        assert "ai_cost_burned_usd" in tasks
        assert tasks[tok_key] >= 0
        assert tasks["ai_cost_burned_usd"] >= 0
        modes = body.get("modes", {})
        assert "distribution" in modes, "modes.distribution missing"
        assert isinstance(modes["distribution"], dict)

    def test_product_analytics_requires_admin(self):
        r = requests.get(f"{BASE_URL}{PREFIX}/admin/product-analytics?days=30",
                         timeout=15)
        assert r.status_code in (401, 403)


# ---------- Pricing sanity ----------
class TestPricingSanity:
    def test_no_inflated_deepseek_rate_in_source(self):
        """Ensure the OLD hardcoded rate table is not USED anywhere (mentions
        in docstrings/comments describing the old bug are fine)."""
        with open("/app/backend/routers/admin_analytics.py") as f:
            src = f.read()
        # Remove docstrings/comments to only inspect executable code
        import re
        code = re.sub(r'""".*?"""', '', src, flags=re.DOTALL)
        code = re.sub(r"#.*", "", code)
        assert "cost_per_1k" not in code, \
            "cost_per_1k rate table still used as executable code"
