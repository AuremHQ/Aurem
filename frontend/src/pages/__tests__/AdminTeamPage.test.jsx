/**
 * AdminTeamPage — the Owner's Admin Team screen (/admin/team).
 *
 * This page had no test; a bad merge left a duplicated, half-closed
 * `Promise.all([...])` in its load() and vite build failed with
 * 'Unexpected "const"' while the vitest suite stayed green.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const get = vi.fn();
vi.mock("../../lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, api: { ...real.api, get: (...a) => get(...a), post: vi.fn(() => Promise.resolve({ data: {} })) } };
});

import AdminTeamPage from "../AdminTeamPage.jsx";

const reply = (map) => get.mockImplementation((url) =>
  url in map ? Promise.resolve({ data: map[url] }) : Promise.reject(new Error(`unexpected GET ${url}`)));

beforeEach(() => { get.mockReset(); });

describe("AdminTeamPage", () => {
  it("loads members, invites, screens and access requests, and shows them", async () => {
    reply({
      "/admin/team/members": { members: [{ user_id: "u1", email: "dev@example.test", role: "developer", status: "active", overrides: {} }] },
      "/admin/team/invites": { invites: [] },
      "/admin/team/screens": { screens: [] },
      "/admin/team/access-requests": { requests: [{ request_id: "r1", email: "dev@example.test", screen_key: "users.all", status: "pending" }] },
    });
    render(<AdminTeamPage />);
    await waitFor(() => expect(screen.getByTestId("admin-team-member-row-u1")).toBeTruthy());
    expect(screen.getByTestId("admin-team-access-request-row-r1")).toBeTruthy();
    expect(get.mock.calls.map((c) => c[0]).sort()).toEqual([
      "/admin/team/access-requests", "/admin/team/invites", "/admin/team/members", "/admin/team/screens",
    ]);
  });

  it("shows the server's reason when loading fails", async () => {
    get.mockRejectedValue({ response: { data: { detail: "Admin access required" } } });
    render(<AdminTeamPage />);
    await waitFor(() => expect(screen.getByTestId("admin-team-page-error").textContent).toMatch(/Admin access required/));
  });
});
