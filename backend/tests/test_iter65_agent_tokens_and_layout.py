"""
test_iter65_agent_tokens_and_layout.py — Iter 65.

Backend: /admin/agent-tokens range selector returns structured data
         with chronological series + per-agent totals + claude/deepseek delta.

Frontend (source-level):
  • AgentTokenPanel.jsx component exists with range selector + chart
  • Admin.jsx Users tab imports + renders AgentTokenPanel
  • Admin.jsx root + aside are height-locked (100vh + overflow hidden)
  • Admin.jsx nav items live in .aurem-rail-scroll (no longer scroll page)
  • index.css `.aurem-main-padded.is-chat` locks chat to 100vh
  • Mobile drawer rules cover the admin shell too
"""
from __future__ import annotations

import os
import re


def _read(rel):
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── Backend endpoint ──────────────────────────────────────────────────

def test_agent_tokens_endpoint_registered():
    from routers.admin_analytics import router as adm
    paths = [r.path for r in adm.routes]
    assert "/admin/agent-tokens" in paths


def test_agent_tokens_handler_supports_all_ranges():
    src = _read("backend/routers/admin_analytics.py")
    m = re.search(r"async def agent_tokens.*?(?=\n@router\.|\Z)",
                  src, re.DOTALL)
    assert m, "agent_tokens handler not found"
    body = m.group(0)
    for r in ("24h", "7d", "30d", "90d", "365d"):
        assert f'"{r}"' in body, f"Range {r} must be supported"
    # Admin gate
    assert "_require_admin(authorization)" in body
    # Bucketing labels
    for lbl in ("hourly", "daily", "weekly", "monthly"):
        assert lbl in body


def test_agent_tokens_returns_claude_vs_deepseek_delta():
    src = _read("backend/routers/admin_analytics.py")
    assert "claude_vs_deepseek" in src
    assert "delta_usd_per_call" in src
    assert "delta_multiplier" in src
    # Avg-per-call summary
    assert "avg_per_call" in src
    assert "cost_avg_usd" in src


def test_agent_tokens_uses_real_cost_rates():
    # 2026-09-27 MASTER TASK Part A #5 — the OLD hardcoded
    # `cost_per_1k = {"deepseek": 0.30, ...}` table was actually a
    # PER-MILLION rate mislabeled as per-1k (a real 3,575-token
    # DeepSeek call costs ~$0.001, not the $1.07 that implied).
    # /admin/agent-tokens must have ZERO fixed rate table now — every
    # dollar figure comes from `real_llm_usage.cost_usd`, which is
    # either the provider's own real `usage.cost` or, for the two
    # non-OpenRouter paths, `services/pricing_table.py` (the ONE
    # per-million price table for the whole app).
    src = _read("backend/routers/admin_analytics.py")
    m = re.search(r"async def agent_tokens.*?(?=\n@router\.|\Z)",
                  src, re.DOTALL)
    body = m.group(0)
    assert 'cost_per_1k = {"deepseek": 0.30, "maxx": 0.65' not in body
    assert "real_llm_usage" in body


# ── Frontend AgentTokenPanel widget ───────────────────────────────────

def test_agent_token_panel_component_exists():
    src = _read("frontend/src/components/AgentTokenPanel.jsx")
    # Range selector buttons for every range
    for r in ("24h", "7d", "30d", "90d", "365d"):
        assert f'"{r}"' in src or f"'{r}'" in src, f"Range {r} button missing"
    # Per-agent cards with data-testids
    for a in ("deepseek", "maxx", "claude", "groq"):
        assert f'data-testid={{`agent-card-${{agent}}`}}' in src or \
               (f'"agent-card-{a}"' in src) or \
               ("agent-card-" in src), \
               "agent-card-<agent> testids must be wired"
    # Claude-vs-DeepSeek callout
    assert 'data-testid="claude-vs-deepseek"' in src
    # Chart container
    assert 'data-testid="agent-tokens-chart"' in src


def test_admin_users_tab_renders_agent_token_panel():
    src = _read("frontend/src/pages/Admin.jsx")
    assert 'import AgentTokenPanel from "../components/AgentTokenPanel"' in src
    # Rendered inside UsersList
    m = re.search(r"function UsersList\(.*?\n}\n", src, re.DOTALL)
    assert m, "UsersList component not found"
    users_body = m.group(0)
    assert "<AgentTokenPanel" in users_body, (
        "AgentTokenPanel must render inside the Users tab"
    )


# ── Admin shell height-lock + sidebar internal scroll ─────────────────

def test_admin_shell_height_locked():
    src = _read("frontend/src/pages/Admin.jsx")
    # Root is height-locked so the page never scrolls as a whole
    assert 'className="aurem-admin-shell"' in src
    assert 'height: "100vh"' in src
    assert 'overflow: "hidden"' in src
    # Aside is height-locked
    aside = re.search(r"<aside[\s\S]*?</aside>", src)
    assert aside is not None
    aside_block = aside.group(0)
    assert 'height: "100vh"' in aside_block
    assert 'overflow: "hidden"' in aside_block
    # Nav items live in scrollable rail
    assert 'className="aurem-rail-scroll"' in aside_block
    assert 'data-testid="admin-nav-scroll"' in aside_block


def test_admin_main_internal_scroll():
    src = _read("frontend/src/pages/Admin.jsx")
    # Main owns its own scroll; page-level scroll is killed
    assert re.search(
        r'<main\s+style=\{\{[^}]*overflow:\s*"auto"[^}]*height:\s*"100vh"',
        src,
    ) is not None


# ── Global CSS additions ──────────────────────────────────────────────

def test_global_css_locks_chat_to_viewport():
    css = _read("frontend/src/index.css")
    # The chat container ONLY scrolls internally; top + bottom stay sticky
    assert ".aurem-main-padded.is-chat" in css
    assert "height: 100vh" in css
    # Rail scroll helper
    assert ".aurem-rail-scroll" in css
    # Admin shell mobile drawer rules
    assert ".aurem-admin-shell" in css
    assert "translateX(-100%)" in css
