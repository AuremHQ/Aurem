/**
 * Integrations.revokeAndEndpoint.test.jsx — UI-fix C8 (2026-10)
 *
 * (a) Non-admin users had no way to revoke their MCP key — the copy
 *     pointed at "Admin → API Keys", which they can't reach. There's
 *     already a user-scoped DELETE /mcp/keys/{tail} endpoint
 *     (routers/mcp.py::revoke_mcp_key); wire a real button to it.
 * (b) The backend's endpoint falls back to the hardcoded production
 *     URL when AUREM_PUBLIC_BASE_URL isn't set (true on preview) — the
 *     page must show the env-correct API_BASE-derived URL instead.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), delete: vi.fn() },
  getToken: () => "tok",
  API_BASE: "https://bin-context-pat.preview.emergentagent.com/api/aurem-dev",
}));
vi.mock("../../components/Toast", () => ({ toast: vi.fn() }));

import Integrations from "../Integrations.jsx";
import { api } from "../../lib/api";

const WRONG_PROD_ENDPOINT = "https://auremcto.com/api/aurem-dev/mcp";
const BACKEND_RESPONSE = {
  api_key: "sk-aurem-abcdefgh-1234",
  api_key_new: false,
  endpoint: WRONG_PROD_ENDPOINT,
  config_json: { mcpServers: { ora: { url: WRONG_PROD_ENDPOINT, headers: {} } } },
  vscode_config_json: { servers: { ora: { url: WRONG_PROD_ENDPOINT, headers: {} } } },
  claude_code_cli: `claude mcp add --transport http ora ${WRONG_PROD_ENDPOINT}`,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ data: BACKEND_RESPONSE });
  api.delete.mockResolvedValue({ data: { ok: true, revoked: 1 } });
});

function renderPage() {
  return render(<MemoryRouter><Integrations /></MemoryRouter>);
}

describe("UI-fix C8 · MCP key revoke + env-correct endpoint", () => {
  it("(b) shows the preview's own API_BASE endpoint, not the backend's hardcoded production fallback", async () => {
    renderPage();
    await screen.findByTestId("api-key-card");
    expect(screen.queryByText(WRONG_PROD_ENDPOINT)).not.toBeInTheDocument();
  });

  it("(a) Revoke / regenerate calls the user-scoped DELETE /mcp/keys/{tail} endpoint and re-mints", async () => {
    renderPage();
    await screen.findByTestId("api-key-card");
    fireEvent.click(screen.getByTestId("api-key-revoke-btn"));
    fireEvent.click(await screen.findByTestId("api-key-revoke-confirm-confirm"));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith("/mcp/keys/1234"));
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  });

  it("no longer tells users to go to Admin → API Keys", async () => {
    renderPage();
    await screen.findByTestId("api-key-card");
    expect(screen.queryByText(/Admin.*API Keys/i)).not.toBeInTheDocument();
  });
});
