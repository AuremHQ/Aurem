"""Regression tests for the 2026-09-12 OpenRouter header/feature-tagging task.

Verifies:
  1. call_openrouter_model builds headers with HTTP-Referer=https://auremcto.com
     and X-Title="AUREM - <feature>" (or fallback "AUREM" when feature=None).
  2. ora_chat.providers._headers() has the same behavior; falls back to
     "AUREM - ORA Chat" when no feature given.
  3. Static X-Title strings in vision_describe / advisor_vision /
     mermaid_diagram files match the required per-feature branding.
  4. Regression check on _call_claude / _call_glm / _call_deepseek /
     _call_longcat signatures — each must accept a `feature` kwarg
     (i.e. changes wired through the provider layer).
  5. Router-level budget-gate call sites still present (regression
     grep for assert_has_budget / assert_has_task_budget).
  6. Health / basic API smoke: backend serves /api/health (or fallback)
     without 500s; protected routes return 401 (auth required), not 500.
"""

import inspect
import os
import re
import sys
from pathlib import Path

import pytest
import requests

# Ensure backend importable
sys.path.insert(0, "/app/backend")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
BACKEND_DIR = Path("/app/backend")


# ---------------------------------------------------------------------------
# 1. call_openrouter_model header logic
# ---------------------------------------------------------------------------
class TestOpenRouterClientHeaders:
    def test_referer_constant_is_auremcto(self):
        from services.llm import openrouter_client
        assert openrouter_client._OPENROUTER_REFERER == "https://auremcto.com"

    def test_call_openrouter_model_accepts_feature_kwarg(self):
        from services.llm.openrouter_client import call_openrouter_model
        sig = inspect.signature(call_openrouter_model)
        assert "feature" in sig.parameters
        assert sig.parameters["feature"].default is None

    def test_header_x_title_uses_feature_when_provided(self):
        """We can't easily run the async function without hitting network;
        instead verify the header construction logic via source inspection."""
        src = (BACKEND_DIR / "services/llm/openrouter_client.py").read_text()
        assert '"X-Title": f"AUREM - {feature}" if feature else "AUREM"' in src
        assert '"HTTP-Referer": _OPENROUTER_REFERER' in src


# ---------------------------------------------------------------------------
# 2. ora_chat.providers._headers behavior
# ---------------------------------------------------------------------------
class TestOraChatProvidersHeaders:
    def test_headers_default_ora_chat_title(self):
        from services.ora_chat import providers
        # _headers may require an api key; patch a dummy via env if needed
        h = providers._headers()
        assert h.get("HTTP-Referer") == "https://auremcto.com"
        assert h.get("X-Title") == "AUREM - ORA Chat"

    def test_headers_feature_overrides_title(self):
        from services.ora_chat import providers
        h = providers._headers(feature="Hallucination Classifier")
        assert h.get("X-Title") == "AUREM - Hallucination Classifier"

    def test_stream_call_and_one_shot_accept_feature(self):
        from services.ora_chat import providers
        for fn_name in ("stream_call", "one_shot"):
            fn = getattr(providers, fn_name)
            assert "feature" in inspect.signature(fn).parameters, \
                f"{fn_name} missing 'feature' kwarg"


# ---------------------------------------------------------------------------
# 3. Static X-Title strings for feature-specific services
# ---------------------------------------------------------------------------
class TestFeatureXTitleLiterals:
    @pytest.mark.parametrize("relpath,expected_title", [
        ("services/mermaid_diagram.py",  "AUREM - Mermaid Diagram"),
        ("services/vision_describe.py",  "AUREM - Vision Describe"),
        ("services/advisor_vision.py",   "AUREM - Advisor Vision"),
    ])
    def test_x_title_present(self, relpath, expected_title):
        text = (BACKEND_DIR / relpath).read_text()
        assert f'"X-Title": "{expected_title}"' in text, \
            f"{relpath} missing expected X-Title '{expected_title}'"
        # HTTP-Referer must be hardcoded to auremcto.com
        assert "https://auremcto.com" in text, \
            f"{relpath} missing hardcoded auremcto.com Referer"


# ---------------------------------------------------------------------------
# 4. Provider fanout accepts feature kwarg
# ---------------------------------------------------------------------------
class TestProviderFanoutFeatureParam:
    def test_providers_have_feature_param(self):
        from services.llm import openrouter_providers as op
        for name in ("_call_claude", "_call_glm", "_call_longcat", "_call_deepseek"):
            fn = getattr(op, name)
            sig = inspect.signature(fn)
            assert "feature" in sig.parameters, f"{name} missing feature kwarg"

    def test_call_llm_with_meta_feature_param(self):
        from services.llm._meta import call_llm_with_meta
        assert "feature" in inspect.signature(call_llm_with_meta).parameters

    def test_agents_role_features_wired(self):
        text = (BACKEND_DIR / "services/agents.py").read_text()
        for role in ("Reader Agent", "Coder Agent", "Reviewer Agent", "Security Agent"):
            assert f'feature="{role}"' in text, f"agents.py missing feature='{role}'"

    def test_loop_engine_planning_feature(self):
        text = (BACKEND_DIR / "services/loop_engine.py").read_text()
        assert 'feature="Loop Planning"' in text

    def test_loop_verify_selfheal_feature(self):
        text = (BACKEND_DIR / "services/loop_verify.py").read_text()
        assert 'feature="Self-Heal Retry"' in text


# ---------------------------------------------------------------------------
# 5. Budget-gate call sites (regression check — not modified this session)
# ---------------------------------------------------------------------------
class TestBudgetGateCallSitesPresent:
    @pytest.mark.parametrize("relpath,needle", [
        ("routers/chat/stream.py",         "assert_has_budget(user["),
        ("routers/chat/turn.py",           "assert_has_budget(user["),
        ("routers/diagram.py",             "assert_has_task_budget(user["),
        ("routers/upload.py",              "assert_has_budget(user["),
        ("routers/cto_projects/tasks.py",  "assert_has_task_budget"),
    ])
    def test_budget_gate_still_wired(self, relpath, needle):
        text = (BACKEND_DIR / relpath).read_text()
        assert needle in text, f"{relpath} lost budget-gate call `{needle}`"


# ---------------------------------------------------------------------------
# 6. Backend smoke — API up, no 500s on public/protected routes
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not BASE_URL, reason="REACT_APP_BACKEND_URL not set")
class TestBackendApiSmoke:
    def test_backend_reachable(self):
        # any 2xx / 3xx / 4xx is fine — we only fail on 5xx / connection err
        r = requests.get(f"{BASE_URL}/api/health", timeout=15)
        assert r.status_code < 500, f"health returned 5xx: {r.status_code} {r.text[:200]}"

    def test_chat_stream_requires_auth_not_500(self):
        # POST without auth — must be 401/403/422 (any 4xx), never 500
        r = requests.post(
            f"{BASE_URL}/api/chat/stream",
            json={"message": "hi"},
            timeout=15,
        )
        assert 400 <= r.status_code < 500, \
            f"chat/stream returned {r.status_code}: {r.text[:200]}"

    def test_diagram_requires_auth_not_500(self):
        r = requests.post(
            f"{BASE_URL}/api/diagram",
            json={"prompt": "flowchart of A -> B"},
            timeout=15,
        )
        assert 400 <= r.status_code < 500, \
            f"diagram returned {r.status_code}: {r.text[:200]}"

    def test_upload_requires_auth_not_500(self):
        r = requests.post(f"{BASE_URL}/api/upload", timeout=15)
        assert 400 <= r.status_code < 500, \
            f"upload returned {r.status_code}: {r.text[:200]}"
