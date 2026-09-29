/**
 * AcceptInvite.phase1.test.jsx — 2026-02 · Phase 1 (Team RBAC)
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  setToken: vi.fn(),
  setUser: vi.fn(),
}));
import { api } from "../../lib/api";
import AcceptInvite from "../AcceptInvite.jsx";

afterEach(() => cleanup());
beforeEach(() => { api.get.mockReset(); api.post.mockReset(); });

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AcceptInvite />
    </MemoryRouter>,
  );
}

describe("AcceptInvite — public invite acceptance page", () => {
  it("shows an invalid-link message for a bad/expired token", async () => {
    api.get.mockResolvedValue({ data: { valid: false } });
    renderAt("/team/accept-invite?token=bad-token");
    expect(await screen.findByTestId("accept-invite-invalid")).toBeTruthy();
  });

  it("shows the password-setup form for a valid invite and submits it", async () => {
    api.get.mockResolvedValue({ data: { valid: true, email: "new@example.com", role_label: "viewer" } });
    api.post.mockResolvedValue({ data: { ok: true, token: "tok", user: { email: "new@example.com" } } });
    renderAt("/team/accept-invite?token=good-token");
    expect(await screen.findByTestId("accept-invite-role")).toHaveTextContent("new@example.com");
    fireEvent.change(screen.getByTestId("accept-invite-password-input"), { target: { value: "GoodPass123!" } });
    fireEvent.change(screen.getByTestId("accept-invite-confirm-input"), { target: { value: "GoodPass123!" } });
    fireEvent.click(screen.getByTestId("accept-invite-submit-btn"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/team/accept-invite", { token: "good-token", password: "GoodPass123!" },
    ));
  });

  it("blocks submit with a client-side message when passwords don't match (still validated again server-side)", async () => {
    api.get.mockResolvedValue({ data: { valid: true, email: "new@example.com", role_label: "viewer" } });
    renderAt("/team/accept-invite?token=good-token");
    await screen.findByTestId("accept-invite-form");
    fireEvent.change(screen.getByTestId("accept-invite-password-input"), { target: { value: "GoodPass123!" } });
    fireEvent.change(screen.getByTestId("accept-invite-confirm-input"), { target: { value: "Different123!" } });
    fireEvent.click(screen.getByTestId("accept-invite-submit-btn"));
    expect(await screen.findByTestId("accept-invite-error")).toHaveTextContent("don't match");
    expect(api.post).not.toHaveBeenCalled();
  });
});
