/**
 * OraDirect.pinGate.phase3.test.jsx — 2026-02 · PIN-gate regression fix
 *
 * Root cause: `/ora` was wrapped in <PrivateRoute> (Gap Register #42)
 * which (a) bounces unauthenticated visitors to /login before the PIN
 * pad ever renders, and (b) OraDirect's own gate treated "any app-wide
 * token exists" as "PIN already entered" — a token minted by the
 * REGULAR login form (required just to get past PrivateRoute) counts
 * as a token, so the PIN was satisfied before the page even mounted.
 *
 * These tests lock in the fix directly against the real
 * `pages/OraDirect.jsx` component (not a re-implemented copy):
 *   1. A visitor with zero token sees the PIN pad, never the chat.
 *   2. A wrong PIN shows an error and leaves the PIN pad up.
 *   3. A correct PIN unlocks the chat and records a PIN-specific
 *      marker distinct from the plain "a token exists" check.
 *   4. A visitor who already holds a valid token from elsewhere
 *      (e.g. the regular dashboard login) — simulating the exact
 *      bypass this fix closes — still gets the PIN pad, not the chat.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  getToken: vi.fn(),
  setToken: vi.fn(),
}));
// Heavy children irrelevant to the auth gate itself — stub them out
// so the post-unlock ChatShell mounts cheaply in jsdom.
vi.mock("../../components/OraChatHouseRulesPanel", () => ({ default: () => null }));
vi.mock("../../components/VideoGenPanel", () => ({ default: () => null }));
vi.mock("../../components/OraPreviewPanel", () => ({ default: () => null }));
vi.mock("streamdown", () => ({ Streamdown: () => null }));

import { api, getToken, setToken } from "../../lib/api";
import OraDirect from "../OraDirect.jsx";

let currentToken = null;

beforeEach(() => {
  currentToken = null;
  localStorage.clear();
  api.get.mockReset();
  api.post.mockReset();
  getToken.mockImplementation(() => currentToken);
  setToken.mockImplementation((t) => { currentToken = t; });
  api.get.mockImplementation((path) => {
    if (path === "/auth/me") return Promise.resolve({ data: { email: "founder@aurem.dev" } });
    if (path === "/ora-chat/pin/status") return Promise.resolve({ data: { pin_set: true } });
    if (path === "/ora-chat/sessions") return Promise.resolve({ data: { sessions: [] } });
    if (path === "/ora-chat/usage") return Promise.resolve({ data: { budget: {} } });
    return Promise.resolve({ data: {} });
  });
  api.post.mockImplementation((path) => Promise.resolve({ data: {} }));
});

afterEach(() => cleanup());

describe("OraDirect — /ora PIN gate (Phase 3 regression fix)", () => {
  it("unauthenticated visitor (no token at all) sees the PIN pad, not the chat", async () => {
    render(<OraDirect />);
    expect(await screen.findByTestId("ora-direct-pin")).toBeTruthy();
    expect(screen.queryByTestId("ora-logout")).toBeNull();
  });

  it("wrong PIN shows an error and leaves the PIN pad up", async () => {
    api.post.mockImplementation((path) => {
      if (path === "/ora-chat/pin-login") {
        const err = new Error("invalid");
        err.response = { data: { detail: { error: "invalid_pin", attempts_remaining: 4 } } };
        return Promise.reject(err);
      }
      return Promise.resolve({ data: {} });
    });
    render(<OraDirect />);
    const input = await screen.findByTestId("pin-input");
    fireEvent.change(input, { target: { value: "wrong-pin-guess" } });
    fireEvent.click(screen.getByTestId("pin-submit-btn"));
    expect(await screen.findByTestId("pin-error")).toHaveTextContent("Wrong PIN");
    expect(screen.getByTestId("ora-direct-pin")).toBeTruthy();
    expect(screen.queryByTestId("ora-logout")).toBeNull();
  });

  it("correct PIN unlocks the chat and records a PIN-specific marker", async () => {
    api.post.mockImplementation((path) => {
      if (path === "/ora-chat/pin-login") {
        return Promise.resolve({
          data: { ok: true, token: "minted-pin-token", expires_in: 604800,
                   user: { email: "founder@aurem.dev", is_admin: true } },
        });
      }
      return Promise.resolve({ data: {} });
    });
    render(<OraDirect />);
    const input = await screen.findByTestId("pin-input");
    fireEvent.change(input, { target: { value: "correct-pin-1234" } });
    fireEvent.click(screen.getByTestId("pin-submit-btn"));
    await waitFor(() => expect(screen.queryByTestId("ora-direct-pin")).toBeNull());
    expect(setToken).toHaveBeenCalledWith("minted-pin-token");
    // The fix's whole point: PIN success is tracked separately from
    // "a token exists" so a differently-sourced token can never
    // impersonate a passed PIN gate.
    expect(localStorage.getItem("ora_pin_token")).toBe("minted-pin-token");
  });

  it("a visitor who already holds a token from elsewhere (regular login) still gets the PIN pad — the exact bypass this fix closes", async () => {
    // Simulate: founder logged in normally elsewhere in the app, so a
    // valid app-wide token already sits in the shared slot BEFORE this
    // component ever mounts. Old code treated `!!getToken()` alone as
    // "PIN already entered" and skipped straight to the chat.
    currentToken = "regular-login-token-unrelated-to-ora-pin";
    render(<OraDirect />);
    expect(await screen.findByTestId("ora-direct-pin")).toBeTruthy();
    expect(screen.queryByTestId("ora-logout")).toBeNull();
  });

  it("a visitor with a valid session but no personal PIN set yet is prompted to set one first, not left unprotected", async () => {
    currentToken = "regular-login-token-no-personal-pin";
    api.get.mockImplementation((path) => {
      if (path === "/auth/me") return Promise.resolve({ data: { email: "founder@aurem.dev" } });
      if (path === "/ora-chat/pin/status") return Promise.resolve({ data: { pin_set: false } });
      return Promise.resolve({ data: {} });
    });
    render(<OraDirect />);
    expect(await screen.findByTestId("ora-direct-pin-setup")).toBeTruthy();
    expect(screen.queryByTestId("ora-direct-pin")).toBeNull();
    expect(screen.queryByTestId("ora-logout")).toBeNull();
  });
});
