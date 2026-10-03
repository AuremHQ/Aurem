"""Check Library v2 / Group 5 (L11.1, L11.3): fires on bad fixtures, silent on clean."""
from __future__ import annotations

from services.checkup_checks_abuse import check_ai_cost_protection, check_auth_rate_limit

EXPRESS_LOGIN = "const app = require('express')();\napp.post('/api/login', async (req, res) => { res.json(await login(req.body)); });\n"
FASTAPI_LOGIN = "@router.post('/auth/login')\nasync def login(body):\n    return await authenticate(body)\n"
LLM = "import OpenAI from 'openai';\nexport async function POST(req){\n  const r = await client.chat.completions.create({ model: 'x', messages });\n  return Response.json(r);\n}\n"


# ── L11.1 ─────────────────────────────────────────────────────────────
def test_login_without_any_limiter_fires_high():
    items = check_auth_rate_limit({"server/auth.js": EXPRESS_LOGIN, "api/auth.py": FASTAPI_LOGIN})
    assert sorted(i["evidence"]["file"] for i in items) == ["api/auth.py", "server/auth.js"]
    assert all(i["rule_id"] == "auth_no_rate_limit" and i["severity"] == "high" for i in items)
    assert [i for i in items if i["evidence"]["file"] == "server/auth.js"][0]["evidence"]["line"] == 2


def test_next_route_file_counts_as_login_endpoint():
    [item] = check_auth_rate_limit({"app/api/auth/login/route.ts": "export async function POST(){ return Response.json({}) }"})
    assert item["evidence"]["line"] == 1


def test_login_with_limiter_anywhere_is_silent():
    assert check_auth_rate_limit({"server/auth.js": EXPRESS_LOGIN, "server/app.js": "app.use(rateLimit({ windowMs: 60000, max: 5 }));"}) == []
    assert check_auth_rate_limit({"api/auth.py": FASTAPI_LOGIN, "requirements.txt": "fastapi\nslowapi\n"}) == []


def test_hosted_auth_and_non_auth_projects_are_silent():
    hosted = "import { createClient } from '@supabase/supabase-js';\nawait supabase.auth.signInWithPassword({ email, password });\n"
    assert check_auth_rate_limit({"src/Login.jsx": hosted, "server/items.js": "app.post('/api/items', h)"}) == []
    assert check_auth_rate_limit({"tests/login.test.js": EXPRESS_LOGIN, "scripts/seed.js": EXPRESS_LOGIN}) == []


# ── L11.3 ─────────────────────────────────────────────────────────────
def test_llm_call_without_quota_fires_critical_with_money_impact():
    [item] = check_ai_cost_protection({"app/api/chat/route.ts": LLM})
    assert item["rule_id"] == "ai_calls_no_quota" and item["severity"] == "critical"
    assert item["confidence"] == "high" and "bill" in item["title_plain"] + item["impact_plain"]
    assert item["evidence"]["line"] == 3


def test_llm_call_with_quota_or_limiter_beside_it_is_silent():
    guarded = LLM.replace("const r", "if (await usedToday(user) > DAILY_LIMIT) return new Response('quota', {status: 429});\n  const r")
    limited = LLM + "const limiter = new Ratelimit({});\n"
    assert check_ai_cost_protection({"app/api/chat/route.ts": guarded, "app/api/x/route.ts": limited}) == []


def test_limits_elsewhere_make_it_unverified_not_red():
    tc = {"app/api/chat/route.ts": LLM, "lib/billing.ts": "export const checkUsage = () => quota.remaining(user);"}
    [item] = check_ai_cost_protection(tc)
    assert item["confidence"] == "unverified"


def test_client_side_scripts_tests_and_non_llm_are_silent():
    assert check_ai_cost_protection({"src/components/Chat.jsx": LLM, "scripts/gen.py": LLM,
                                     "tests/llm.test.js": LLM, "server/app.js": "fetch('https://api.stripe.com/v1')"}) == []
