/**
 * Admin shell — what happens when GET /admin/me fails.
 *
 * Bug (found in the 2026-09-29 admin audit): any failure other than a
 * 401 showed "Admin access required" and redirected to /dashboard, so a
 * deploy, restart, timeout or 500 made a real admin look like a
 * non-admin and threw them out of the console.
 *
 * Contract now: 401 -> login, 403 -> dashboard, anything else (5xx,
 * timeout, no response) -> stay on the admin shell with a Retry button.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { adminMeFailureAction } from "../../lib/adminAccess";

const get = vi.fn();
vi.mock("../../lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, api: { ...real.api, get: (...a) => get(...a) } };
});

import Admin from "../Admin.jsx";

const renderAdmin = () =>
  render(
    <MemoryRouter initialEntries={["/admin"]}>
      <Routes>
        <Route path="/admin" element={<Admin initialTab="overview" />} />
        <Route path="/dashboard" element={<div data-testid="at-dashboard" />} />
        <Route path="/login" element={<div data-testid="at-login" />} />
      </Routes>
    </MemoryRouter>,
  );

const failWith = (status) =>
  get.mockImplementation((path) =>
    path === "/admin/me"
      ? Promise.reject(status ? { response: { status } } : new Error("Network Error"))
      : new Promise(() => {}),
  );

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q) => ({
    matches: false, media: q, onchange: null,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent() { return false; },
  }));
  get.mockReset();
  localStorage.clear();
  localStorage.setItem("aurem_token", "t");
});

describe("adminMeFailureAction", () => {
  it.each([
    [401, "login"],
    [403, "denied"],
    [500, "retry"],
    [502, "retry"],
    [503, "retry"],
    [404, "retry"],
    [undefined, "retry"],
  ])("status %s -> %s", (status, expected) => {
    const err = status ? { response: { status } } : new Error("timeout");
    expect(adminMeFailureAction(err)).toBe(expected);
  });
});

describe("<Admin> when /admin/me fails", () => {
  it("503 keeps the admin on the shell and offers a retry", async () => {
    failWith(503);
    renderAdmin();
    expect(await screen.findByTestId("admin-me-error")).toBeTruthy();
    expect(screen.queryByTestId("at-dashboard")).toBeNull();
    expect(screen.queryByTestId("at-login")).toBeNull();
  });

  it("a network error (no response) also stays put", async () => {
    failWith(undefined);
    renderAdmin();
    expect(await screen.findByTestId("admin-me-error")).toBeTruthy();
    expect(screen.queryByTestId("at-dashboard")).toBeNull();
  });

  it("Retry calls /admin/me again", async () => {
    failWith(500);
    renderAdmin();
    await screen.findByTestId("admin-me-error");
    const before = get.mock.calls.filter(([p]) => p === "/admin/me").length;
    fireEvent.click(screen.getByTestId("admin-me-retry"));
    await waitFor(() =>
      expect(get.mock.calls.filter(([p]) => p === "/admin/me").length).toBe(before + 1),
    );
  });

  it("403 sends a non-admin to the dashboard", async () => {
    failWith(403);
    renderAdmin();
    expect(await screen.findByTestId("at-dashboard")).toBeTruthy();
  });

  it("401 clears the session and sends the user to login", async () => {
    failWith(401);
    renderAdmin();
    expect(await screen.findByTestId("at-login")).toBeTruthy();
    expect(localStorage.getItem("aurem_token")).toBeNull();
  });
});
