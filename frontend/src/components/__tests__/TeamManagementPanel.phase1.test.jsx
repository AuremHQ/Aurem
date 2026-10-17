/**
 * TeamManagementPanel.phase1.test.jsx — 2026-02 · Phase 1 (Team RBAC)
 *
 * Frontend-only coverage for the Team settings panel: owner sees the
 * invite form + matrix editor; a non-owner/non-admin (server truth
 * from /team/me) sees the "no access" message instead — this is UX
 * only, real enforcement is proven against the live backend in
 * backend/tests/test_phase1_team_rbac_live_http.py.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));
import { api } from "../../lib/api";
import TeamManagementPanel from "../TeamManagementPanel.jsx";

const RESOURCES = { domain: "Domain", team_management: "Team & permissions" };
const PRESETS = { admin: {}, editor: {}, viewer: {} };

beforeEach(() => {
  api.get.mockReset(); api.post.mockReset(); api.put.mockReset();
});
afterEach(() => cleanup());

describe("TeamManagementPanel — owner view", () => {
  beforeEach(() => {
    api.get.mockImplementation((path) => {
      // CI-REPAIR (product changed, not a regression) — 2026-09-28 MASTER
      // TASK Batch 2 (c) added an outer `team_feature_enabled` paywall gate
      // in TeamManagementPanel.jsx (verified against source: `if (!entitled)
      // return <...team-panel-upgrade-required.../>`) checked BEFORE the
      // owner/canManage branch this describe block exercises. Without it
      // every mock here rendered the upgrade card instead. Added here so
      // the real owner-view behaviour below is actually reached.
      if (path === "/team/me") return Promise.resolve({ data: { is_owner: true, team_feature_enabled: true, permissions: { team_management: "read_write" } } });
      if (path === "/team/resources") return Promise.resolve({ data: { resources: RESOURCES, role_presets: PRESETS } });
      if (path === "/team/members") return Promise.resolve({ data: { members: [
        { member_id: "m1", email: "teammate@example.com", status: "pending", role_label: "viewer",
          permissions: { domain: "read", team_management: "none" } },
      ] } });
      return Promise.resolve({ data: {} });
    });
  });

  it("shows the invite form and the existing member list", async () => {
    render(<TeamManagementPanel />);
    expect(await screen.findByTestId("team-invite-email-input")).toBeTruthy();
    expect(await screen.findByTestId("team-member-row-m1")).toBeTruthy();
    expect(screen.getByTestId("team-status-pending")).toBeTruthy();
  });

  it("submits a real invite request with the chosen email + role", async () => {
    api.post.mockResolvedValue({ data: { ok: true, member_id: "m2", email_sent: true } });
    render(<TeamManagementPanel />);
    const input = await screen.findByTestId("team-invite-email-input");
    fireEvent.change(input, { target: { value: "new-person@example.com" } });
    fireEvent.click(screen.getByTestId("team-invite-submit-btn"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/team/invite", { email: "new-person@example.com", role_label: "viewer" },
    ));
  });

  it("editing a member's per-resource access PUTs the real permissions endpoint", async () => {
    api.put.mockResolvedValue({ data: { ok: true, permissions: {} } });
    render(<TeamManagementPanel />);
    fireEvent.click(await screen.findByTestId("team-member-matrix-toggle-m1"));
    const select = await screen.findByTestId("team-member-perm-m1-domain");
    fireEvent.change(select, { target: { value: "write" } });
    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      "/team/members/m1/permissions", { permissions: { domain: "write" } },
    ));
  });
});

describe("TeamManagementPanel — non-owner/non-admin view", () => {
  it("shows a no-access message instead of the invite form", async () => {
    api.get.mockImplementation((path) => {
      // Same entitlement gate as the owner-view block above — must be
      // true here too, otherwise this ALSO hits the upgrade-required
      // card (wrong reason for "no access") instead of the real
      // canManage=false → team-panel-no-access branch this test targets.
      if (path === "/team/me") return Promise.resolve({ data: { is_owner: false, team_feature_enabled: true, permissions: { team_management: "read" } } });
      if (path === "/team/resources") return Promise.resolve({ data: { resources: RESOURCES, role_presets: PRESETS } });
      if (path === "/team/members") return Promise.reject({ response: { status: 403 } });
      return Promise.resolve({ data: {} });
    });
    render(<TeamManagementPanel />);
    expect(await screen.findByTestId("team-panel-no-access")).toBeTruthy();
    expect(screen.queryByTestId("team-invite-email-input")).toBeNull();
  });
});
