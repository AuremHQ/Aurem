"""
tests/test_mcp_ora_chat_admin.py — 2026-09-28 ORA Chat MCP server (Part A).

Regression coverage for:
  - GET  /mcp/ora-chat            (manifest)
  - POST /mcp/ora-chat            (JSON-RPC: initialize/tools/list/tools/call)
  - POST /admin/gateway/keys      (create — admin only)
  - GET  /admin/gateway/keys      (list — admin only)
  - POST /admin/gateway/keys/{id}/revoke
  - Non-admin rejection (server-side, not just UI)
  - Per-key rate limiting

Known, pre-existing, out-of-scope gap (NOT part of this feature): ORA
Chat's own dedicated LLM config (role="chat", services/llm_config_store.py)
has no working API key in this environment, so `ask_ora_chat` cannot
produce a real reply here — it correctly surfaces `llm_api_key_missing`
as a tool_failed JSON-RPC error instead of silently succeeding. The
test below accepts either a real reply OR that specific error, so it
will keep passing once the founder configures a working key, without
masking a DIFFERENT failure.
"""
import os
import random

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")
API = f"{BASE_URL}/api/aurem-dev"
MCP_URL = f"{API}/mcp/ora-chat"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
    }, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def nonadmin_token():
    email = f"pytest-mcp-nonadmin-{random.randint(100000, 999999)}@example.com"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": "NonAdmin2026!", "name": "N",
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    return r.json()["token"]


@pytest.fixture()
def mcp_key(admin_token):
    r = requests.post(f"{API}/admin/gateway/keys",
                       json={"scope": "mcp", "label": "pytest key"},
                       headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    yield data["key"], data["key_doc"]["key_id"]
    requests.post(f"{API}/admin/gateway/keys/{data['key_doc']['key_id']}/revoke?scope=mcp",
                   headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)


class TestManifestAndAuth:
    def test_manifest_lists_three_tools(self):
        r = requests.get(MCP_URL, timeout=10)
        assert r.status_code == 200
        names = {t["name"] for t in r.json()["tools"]}
        assert names == {"ask_ora_chat", "list_ora_conversations", "get_ora_conversation"}

    def test_initialize_does_not_need_auth(self):
        r = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}, timeout=10)
        assert r.status_code == 200
        assert r.json()["result"]["protocolVersion"]

    def test_tools_list_rejects_missing_auth(self):
        r = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=10)
        assert r.status_code == 401

    def test_tools_list_rejects_garbage_key(self):
        r = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                           headers={"Authorization": "Bearer oraadm_mcp_not_a_real_key"}, timeout=10)
        assert r.status_code == 401 or r.status_code == 403

    def test_tools_list_works_with_valid_key(self, mcp_key):
        raw, _ = mcp_key
        r = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                           headers={"Authorization": f"Bearer {raw}"}, timeout=10)
        assert r.status_code == 200
        assert len(r.json()["result"]["tools"]) == 3


class TestKeyAdminOnly:
    def test_nonadmin_cannot_create_key(self, nonadmin_token):
        r = requests.post(f"{API}/admin/gateway/keys",
                           json={"scope": "mcp", "label": "hacker"},
                           headers={"Authorization": f"Bearer {nonadmin_token}"}, timeout=10)
        assert r.status_code == 403

    def test_nonadmin_cannot_list_keys(self, nonadmin_token):
        r = requests.get(f"{API}/admin/gateway/keys?scope=mcp",
                          headers={"Authorization": f"Bearer {nonadmin_token}"}, timeout=10)
        assert r.status_code == 403

    def test_admin_can_create_list_revoke(self, admin_token):
        r = requests.post(f"{API}/admin/gateway/keys",
                           json={"scope": "mcp", "label": "pytest lifecycle"},
                           headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["key"].startswith("oraadm_mcp_")
        assert "key_hash" not in data["key_doc"]
        key_id = data["key_doc"]["key_id"]

        r2 = requests.get(f"{API}/admin/gateway/keys?scope=mcp",
                           headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
        assert any(k["key_id"] == key_id for k in r2.json()["keys"])

        r3 = requests.post(f"{API}/admin/gateway/keys/{key_id}/revoke?scope=mcp",
                            headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
        assert r3.status_code == 200

        # Revoked key must be rejected by the MCP endpoint itself.
        r4 = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                            headers={"Authorization": f"Bearer {data['key']}"}, timeout=10)
        assert r4.status_code == 403


class TestRateLimit:
    def test_rate_limit_enforced_per_key(self, admin_token):
        r = requests.post(f"{API}/admin/gateway/keys",
                           json={"scope": "mcp", "label": "pytest rate-limit", "rate_limit_per_min": 2},
                           headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
        raw = r.json()["key"]
        key_id = r.json()["key_doc"]["key_id"]
        statuses = []
        for _ in range(4):
            rr = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                headers={"Authorization": f"Bearer {raw}"}, timeout=10)
            statuses.append(rr.status_code)
        assert 429 in statuses, f"expected a 429 among {statuses}"
        requests.post(f"{API}/admin/gateway/keys/{key_id}/revoke?scope=mcp",
                       headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)


class TestTools:
    def test_list_ora_conversations(self, mcp_key):
        raw, _ = mcp_key
        r = requests.post(MCP_URL, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "list_ora_conversations", "arguments": {"limit": 5}},
        }, headers={"Authorization": f"Bearer {raw}"}, timeout=15)
        assert r.status_code == 200
        assert "conversations" in r.json()["result"]["data"]

    def test_get_ora_conversation_not_found(self, mcp_key):
        raw, _ = mcp_key
        r = requests.post(MCP_URL, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_ora_conversation", "arguments": {"conversation_id": "does-not-exist"}},
        }, headers={"Authorization": f"Bearer {raw}"}, timeout=15)
        assert r.status_code == 200
        assert "error" in r.json()

    def test_ask_ora_chat_returns_reply_or_the_known_config_gap(self, mcp_key):
        """Accepts either a real reply (once the ORA-Chat LLM key is
        configured) or the specific llm_api_key_missing tool_failed
        error (the current, known, pre-existing state) — asserts the
        WIRING is correct either way, without masking a different bug."""
        raw, _ = mcp_key
        r = requests.post(MCP_URL, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "ask_ora_chat", "arguments": {"message": "ping"}},
        }, headers={"Authorization": f"Bearer {raw}"}, timeout=65)
        assert r.status_code == 200
        body = r.json()
        if "result" in body:
            assert body["result"]["data"]["reply"]
            assert body["result"]["data"]["conversation_id"]
        else:
            assert "llm_api_key_missing" in body["error"]["message"]

    def test_unknown_tool_name_rejected(self, mcp_key):
        raw, _ = mcp_key
        r = requests.post(MCP_URL, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "delete_everything", "arguments": {}},
        }, headers={"Authorization": f"Bearer {raw}"}, timeout=10)
        assert r.status_code == 200
        assert "error" in r.json()
