"""
tests/test_iter212m174_ide_integration.py

Iter 212m-174 — 8 new MCP tools + /mcp/install-links endpoint +
frontend /integrations page + VS Code extension migration.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/app/backend")

BACKEND_ROOT  = Path("/app/backend")
FRONTEND_ROOT = Path("/app/frontend/src")
VSCODE_ROOT   = Path("/app/vscode-extension")


# ─── PART 1 — 8 new MCP tools ───────────────────────────────────────

def test_mcp_manifest_lists_all_12_tools():
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    for name in [
        "list_projects", "ship_code", "get_task_status", "get_recent_commits",
        "read_repo_file", "list_repo_files", "search_repo", "write_repo_file",
        "run_vanguard_scan", "get_repo_health", "get_repo_structure",
        "get_project_info",
    ]:
        assert f'"name": "{name}"' in src, f"missing tool: {name}"


def test_mcp_tool_dispatch_has_all_12():
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    for handler in [
        "_tool_list_projects", "_tool_ship_code", "_tool_get_task_status",
        "_tool_get_recent_commits", "_tool_read_repo_file",
        "_tool_list_repo_files", "_tool_search_repo", "_tool_write_repo_file",
        "_tool_run_vanguard_scan", "_tool_get_repo_health",
        "_tool_get_repo_structure", "_tool_get_project_info",
    ]:
        assert f"async def {handler}(" in src, f"missing handler: {handler}"


def test_mcp_new_tools_use_bin_context_isolation():
    """Every new repo-scoped tool builds a BINContext via _mcp_ctx_for
    which raises RuntimeError if the project doesn't belong to the caller."""
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    assert "async def _mcp_ctx_for" in src
    assert "from services.bin_context import build_bin_context" in src
    # Every new tool must go through _mcp_ctx_for (or explicitly document
    # why not, but for now every one uses it).
    for handler in [
        "_tool_read_repo_file", "_tool_list_repo_files", "_tool_search_repo",
        "_tool_write_repo_file", "_tool_run_vanguard_scan",
        "_tool_get_repo_health", "_tool_get_repo_structure",
    ]:
        # Locate function body and verify _mcp_ctx_for is called.
        idx = src.find(f"async def {handler}(")
        assert idx != -1, f"handler not found: {handler}"
        # Find next `async def` or EOF
        next_idx = src.find("\nasync def ", idx + 10)
        body = src[idx: next_idx if next_idx != -1 else None]
        assert "_mcp_ctx_for(user_id, project_id)" in body, (
            f"{handler} does not use _mcp_ctx_for for isolation"
        )


def test_mcp_write_file_tool_has_content_and_path():
    """write_repo_file schema must declare content + path as required."""
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    # locate schema for write_repo_file
    idx = src.find('"name": "write_repo_file"')
    assert idx != -1
    block = src[idx: idx + 1500]
    assert '"required": ["project_id", "file_path", "content"]' in block


# ─── PART 5 — /mcp/install-links endpoint ───────────────────────────

def test_install_links_endpoint_registered():
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    assert '@router.get("/install-links")' in src
    assert "async def install_links(" in src


def test_install_links_endpoint_shape():
    """Endpoint must return: endpoint, api_key, config_json,
    vscode_config_json, claude_code_cli, instructions.

    2026-09-26 SECURITY FIX — `cursor`/`vscode` deep-link URLs used
    to be returned here with the raw API key baked into the query
    string, then rendered as a real `<a href>` — meaning the full key
    sat in the page's HTML regardless of whether "Reveal" was ever
    clicked (copyable via "Copy link address", zero interaction).
    Removed; Cursor/VS Code setup is now manual-config-only via the
    same masked-then-reveal ConfigBlock pattern already used
    correctly by Claude Desktop/Claude Code CLI.
    """
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    for k in [
        '"endpoint":', '"api_key":', '"config_json":',
        '"vscode_config_json":', '"claude_code_cli":', '"instructions":',
    ]:
        assert k in src, f"install_links missing return key: {k}"


def test_install_links_never_returns_a_raw_key_deeplink():
    """Regression guard — the cursor://…/vscode:mcp/install deep-link
    schemes (which embedded the unmasked API key directly in a URL)
    must never be reintroduced into this endpoint. Checks for the
    actual code constructs, not just a substring, since the docstring
    above legitimately mentions both scheme names while explaining
    the fix."""
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    assert "cursor_link " not in src
    assert "vscode_link " not in src
    assert '"cursor":          cursor_link' not in src
    assert '"vscode":          vscode_link' not in src


def test_vscode_config_json_uses_servers_not_mcpservers_key():
    """VS Code's real MCP schema top-level key is `servers`, not
    `mcpServers` (that's the Cursor/Claude Desktop/Claude Code
    convention) — confirmed against VS Code's own docs, 2026-09.
    Distinct from `config_json` (used by Cursor/Claude Desktop)."""
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    idx = src.find("vscode_config_json = {")
    assert idx != -1, "vscode_config_json block not found"
    block = src[idx: idx + 300]
    assert '"servers": {' in block


def test_install_links_auto_mints_api_key_when_none_exists():
    """Contract test: install-links code path mints a key when
    mint_if_missing=true and the user has no active key."""
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    assert "mint_if_missing" in src
    assert 'f"sk-aurem-{secrets.token_urlsafe(24)}"' in src
    assert 'api_key_new = True' in src


# ─── PART 2 — /integrations frontend page ───────────────────────────

def test_integrations_page_exists():
    assert (FRONTEND_ROOT / "pages/Integrations.jsx").exists()


def test_integrations_page_has_all_four_tabs():
    src = (FRONTEND_ROOT / "pages/Integrations.jsx").read_text()
    # Tab ids declared in the TABS array.
    for tid in ["cursor", "vscode", "claude_desktop", "claude_code"]:
        assert f'id: "{tid}"' in src, f"tab id missing: {tid}"
    # Tab body renders (Cursor/VSCode/ClaudeDesktop/ClaudeCode sub-components).
    for comp in ["CursorTab", "VSCodeTab", "ClaudeDesktopTab", "ClaudeCodeTab"]:
        assert comp in src, f"tab component missing: {comp}"
    # Testid is built via template literal `integrations-tab-${t.id}`.
    assert "integrations-tab-${t.id}" in src


def test_integrations_page_calls_install_links_endpoint():
    src = (FRONTEND_ROOT / "pages/Integrations.jsx").read_text()
    assert "/mcp/install-links" in src


def test_integrations_page_has_test_connection_button():
    src = (FRONTEND_ROOT / "pages/Integrations.jsx").read_text()
    assert "test-connection-btn" in src
    assert 'api.get("/mcp")' in src or "api.get('/mcp')" in src


def test_integrations_page_has_login_gate_for_anon_users():
    src = (FRONTEND_ROOT / "pages/Integrations.jsx").read_text()
    assert "integrations-login-gate" in src


# ─── PART 3 — Sidebar nav + Landing nav ────────────────────────────

def test_shell_sidebar_has_integrations_link():
    src = (FRONTEND_ROOT / "components/Shell.jsx").read_text()
    assert 'to: "/integrations"' in src
    assert 'testid: "nav-integrations"' in src


def test_landing_nav_has_integrations_link():
    """2026-09-26 — Landing.jsx's nav was extracted into the shared
    MarketingNav.jsx component (used by every public page); the
    Integrations link now lives there instead of inline in
    Landing.jsx."""
    src = (FRONTEND_ROOT / "components/marketing/MarketingNav.jsx").read_text()
    assert 'to="/integrations"' in src
    assert 'data-testid="nav-integrations"' in src


def test_app_jsx_has_integrations_route():
    src = (FRONTEND_ROOT / "App.jsx").read_text()
    assert 'lazy(() => import("./pages/Integrations"))' in src
    assert 'path="/integrations"' in src


# ─── PART 4 — VS Code extension migration ──────────────────────────

def test_vscode_extension_uses_secretstorage_apikey():
    src = (VSCODE_ROOT / "src/extension.ts").read_text()
    assert "context.secrets.store" in src
    assert "context.secrets.get" in src
    assert "SECRET_KEY = 'aurem.apiKey'" in src


def test_vscode_extension_does_not_use_oauth_localhost_callback():
    """OAuth localhost server + vscode_callback URL must be gone."""
    src = (VSCODE_ROOT / "src/extension.ts").read_text()
    assert "createServer" not in src
    assert "vscode_callback" not in src


def test_vscode_extension_routes_through_mcp_json_rpc():
    """Ship action must POST JSON-RPC to /mcp with a `tools/call`."""
    src = (VSCODE_ROOT / "src/extension.ts").read_text()
    assert "MCP_PATH   = '/api/aurem-dev/mcp'" in src
    assert "'tools/call'" in src or '"tools/call"' in src
    assert "jsonrpc: '2.0'" in src or 'jsonrpc: "2.0"' in src


def test_vscode_extension_version_bumped():
    pkg = json.loads((VSCODE_ROOT / "package.json").read_text())
    # 0.3.0 introduces the MCP + sk-aurem migration.
    assert pkg["version"].startswith("0.3.") or pkg["version"] >= "0.3.0"


def test_vscode_extension_prompts_for_sk_aurem_key():
    src = (VSCODE_ROOT / "src/extension.ts").read_text()
    assert "sk-aurem-" in src


# ─── Regression — existing tools + tests still pass ────────────────

def test_existing_4_tools_still_registered_in_dispatch():
    """The original 4 tools MUST still be dispatched (backwards compat)."""
    src = (BACKEND_ROOT / "routers/mcp.py").read_text()
    for name in ["list_projects", "ship_code", "get_task_status", "get_recent_commits"]:
        assert f'"{name}":' in src, f"legacy tool no longer dispatched: {name}"
