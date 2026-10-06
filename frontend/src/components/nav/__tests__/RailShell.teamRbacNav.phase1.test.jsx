/**
 * RailShell.teamRbacNav.phase1.test.jsx — 2026-09-26 (Phase 1 team RBAC)
 *
 * Replaces the earlier Shell.teamRbacNav.phase1.test.jsx, which
 * grepped Shell.jsx's source for a `resource:` tag on its NAV array —
 * but Shell.jsx's own sidebar chrome is dead code for every real app
 * page (all of them render `<Shell chromeless>` + this component,
 * `RailShell`, for the actual nav). That old test was 100%
 * STATIC_GREP *and* verifying the wrong component. This one actually
 * renders RailShell and asserts on the real DOM: the "Team" nav item
 * (data-testid="nav-team") only appears once `/team/me` confirms the
 * caller can manage the team, and stays hidden for a plain member
 * (fail-closed) or while permissions are still loading.
 *
 * Live-browser confirmation of the same behaviour (real signup, real
 * click through the Settings rail icon) was already done manually
 * this session — this test locks it in for CI.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  getToken: vi.fn(() => "fake-token"),
  logout: vi.fn(),
  newSessionId: vi.fn(() => "sess-1"),
}));
vi.mock("../../../lib/chipFlag", () => ({ setChipV2Enabled: vi.fn() }));

import { api } from "../../../lib/api";
import RailShell from "../RailShell.jsx";

afterEach(() => cleanup());
beforeEach(() => { api.get.mockReset(); api.post.mockReset(); });

function mockApiFor({ canManageTeam }) {
  api.get.mockImplementation((url) => {
    if (url === "/auth/me") return Promise.resolve({ data: { user: { tier: "team" } } });
    if (url === "/team/me") {
      return Promise.resolve({
        data: {
          is_owner: canManageTeam,
          team_feature_enabled: canManageTeam,
          permissions: { team_management: canManageTeam ? "read_write" : "none" },
        },
      });
    }
    if (url === "/cto/projects/list") return Promise.resolve({ data: { projects: [] } });
    if (url === "/chat/sessions") return Promise.resolve({ data: { sessions: [] } });
    return Promise.resolve({ data: {} });
  });
}

function renderRail() {
  return render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <RailShell railOnly />
    </MemoryRouter>,
  );
}

async function openSettingsFlyout() {
  fireEvent.click(screen.getByTestId("rail-icon-settings"));
}

describe("RailShell — Phase 1 team-management nav visibility", () => {
  it("shows the Team nav item once /team/me confirms owner/admin access", async () => {
    mockApiFor({ canManageTeam: true });
    renderRail();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/team/me"));
    await openSettingsFlyout();
    expect(await screen.findByTestId("nav-team")).toBeTruthy();
  });

  it("hides the Team nav item for a plain member (fail-closed, not fail-open)", async () => {
    mockApiFor({ canManageTeam: false });
    renderRail();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/team/me"));
    await openSettingsFlyout();
    // Give any (incorrect) fail-open render a tick to show up before asserting absence.
    await waitFor(() => {
      expect(screen.queryByTestId("nav-team")).toBeNull();
    });
  });

  it("hides the Team nav item while /team/me hasn't resolved yet (no premature fail-open)", async () => {
    let resolveTeamMe;
    api.get.mockImplementation((url) => {
      if (url === "/team/me") {
        return new Promise((resolve) => { resolveTeamMe = resolve; });
      }
      return Promise.resolve({ data: {} });
    });
    renderRail();
    await openSettingsFlyout();
    expect(screen.queryByTestId("nav-team")).toBeNull();
    resolveTeamMe({ data: { is_owner: true, tier: "team", team_feature_enabled: true, permissions: { team_management: "read_write" } } });
    expect(await screen.findByTestId("nav-team")).toBeTruthy();
  });
});
