/**
 * TeamInviteAccept — accepting an Admin Team invite.
 *
 * This page had no test, and a bad merge left a stray button and an unmatched
 * </p> in its success block: vite build failed ("Unexpected closing p tag")
 * while the whole vitest suite stayed green, because nothing imported the file.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

const post = vi.fn();
vi.mock("../../lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, api: { ...real.api, post: (...a) => post(...a), get: vi.fn(() => Promise.resolve({ data: {} })) } };
});

import TeamInviteAccept from "../TeamInviteAccept.jsx";

const renderPage = (url) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/team-invite/accept" element={<TeamInviteAccept />} />
        <Route path="/dashboard" element={<div data-testid="at-dashboard" />} />
        <Route path="/admin" element={<div data-testid="at-admin" />} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  post.mockReset();
  localStorage.clear();
  localStorage.setItem("aurem_user", JSON.stringify({ email: "member@example.test" }));
});

describe("TeamInviteAccept", () => {
  it("explains a link with no token", () => {
    renderPage("/team-invite/accept");
    expect(screen.getByTestId("team-invite-accept-missing-token")).toBeTruthy();
  });

  it("shows the success state with one dashboard button and one admin button", async () => {
    post.mockResolvedValue({ data: { role: "developer" } });
    renderPage("/team-invite/accept?token=abc");
    fireEvent.click(screen.getByTestId("team-invite-accept-submit-btn"));
    await waitFor(() => expect(screen.getByTestId("team-invite-accept-success")).toBeTruthy());
    expect(post).toHaveBeenCalledWith("/team-invite/accept", { token: "abc" });
    expect(screen.getAllByTestId("team-invite-accept-go-dashboard-btn")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("team-invite-accept-open-admin-btn"));
    expect(screen.getByTestId("at-admin")).toBeTruthy();
  });

  it("shows the server's reason when accepting fails", async () => {
    post.mockRejectedValue({ response: { data: { detail: "Invite expired" } } });
    renderPage("/team-invite/accept?token=abc");
    fireEvent.click(screen.getByTestId("team-invite-accept-submit-btn"));
    await waitFor(() => expect(screen.getByTestId("team-invite-accept-error").textContent).toMatch(/Invite expired/));
  });
});
