/**
 * OraDirect.reverseEngineerSlash.test.jsx — 2026-10-24
 *
 * `/reverse-engineer <url>` on /ora, ported from ChatPanel.jsx's
 * dashboard version onto the same shared backend endpoints
 * (services/rea_service.py + routers/admin_rea.py). No separate
 * admin check is needed on this page: ora_chat.py's `pin_login` only
 * ever resolves admin/founder accounts, so every /ora session that
 * gets past the PIN pad already carries an admin JWT.
 *
 * Locks in:
 *   1. Typing the command + Enter posts to /admin/rea/analyze (not
 *      the SSE /ora-chat/message path) and renders the ReaAnalysisCard
 *      for the resulting `kind: "rea_job"` message.
 *   2. A backend error (e.g. the SSRF validator's 400) surfaces as a
 *      visible error bubble instead of silently doing nothing.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  getToken: vi.fn(),
  setToken: vi.fn(),
}));
vi.mock("../../components/OraChatHouseRulesPanel", () => ({ default: () => null }));
vi.mock("../../components/VideoGenPanel", () => ({ default: () => null }));
vi.mock("../../components/OraPreviewPanel", () => ({ default: () => null }));
vi.mock("streamdown", () => ({ Streamdown: ({ children }) => <div>{children}</div> }));
vi.mock("../../components/ReaAnalysisCard", () => ({
  default: ({ jobId, url }) => (
    <div data-testid="rea-analysis-card-stub">{jobId}:{url}</div>
  ),
}));

import { api, getToken, setToken } from "../../lib/api";
import OraDirect from "../OraDirect.jsx";

beforeEach(() => {
  localStorage.clear();
  api.get.mockReset();
  api.post.mockReset();
  getToken.mockImplementation(() => "already-authed-ora-token");
  setToken.mockImplementation(() => {});
  localStorage.setItem("ora_pin_token", "already-authed-ora-token");
  api.get.mockImplementation((path) => {
    if (path === "/ora-chat/sessions") return Promise.resolve({ data: { sessions: [] } });
    if (path === "/ora-chat/usage") return Promise.resolve({ data: { budget: {} } });
    return Promise.resolve({ data: {} });
  });
});

afterEach(() => cleanup());

describe("OraDirect — /reverse-engineer slash command", () => {
  it("posts to /admin/rea/analyze and renders the REA card for the assistant turn", async () => {
    api.post.mockImplementation((path, body) => {
      if (path === "/ora-chat/sessions") {
        return Promise.resolve({ data: { session: { session_id: "sess-1" } } });
      }
      if (path === "/admin/rea/analyze") {
        expect(body).toEqual({ url: "https://example.com" });
        return Promise.resolve({ data: { job_id: "rea_abc123", url: "https://example.com", status: "running" } });
      }
      return Promise.resolve({ data: {} });
    });
    render(<OraDirect />);
    const input = await screen.findByTestId("ora-input");
    fireEvent.change(input, { target: { value: "/reverse-engineer https://example.com" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    await waitFor(() => expect(screen.getByTestId("rea-analysis-card-stub")).toHaveTextContent("rea_abc123:https://example.com"));
    // Confirms this did NOT go through the SSE /ora-chat/message path.
    expect(api.post).toHaveBeenCalledWith("/admin/rea/analyze", { url: "https://example.com" });
  });

  it("a rejected URL (e.g. SSRF-guard 400) surfaces as a visible error bubble", async () => {
    api.post.mockImplementation((path) => {
      if (path === "/ora-chat/sessions") {
        return Promise.resolve({ data: { session: { session_id: "sess-1" } } });
      }
      if (path === "/admin/rea/analyze") {
        const err = new Error("bad url");
        err.response = { data: { detail: "That host can't be analyzed (local/private/internal addresses are blocked)." } };
        return Promise.reject(err);
      }
      return Promise.resolve({ data: {} });
    });
    render(<OraDirect />);
    const input = await screen.findByTestId("ora-input");
    fireEvent.change(input, { target: { value: "/reverse-engineer http://127.0.0.1" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    const errBubble = await screen.findByTestId("ora-msg-assistant");
    expect(errBubble).toHaveTextContent("blocked");
    expect(screen.queryByTestId("rea-analysis-card-stub")).toBeNull();
  });
});
