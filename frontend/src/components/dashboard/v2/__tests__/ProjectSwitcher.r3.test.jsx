/**
 * ProjectSwitcher.r3.test.jsx — R3 (Repo Quick-Switch).
 *
 * Named tests (per the R3 spec):
 *   t_switch_repo_a_to_b            — clicking a valid project calls onSelect
 *   t_revoked_repo_non_selectable   — a project with a broken connection
 *                                     status renders disabled + the
 *                                     exact "repo unreachable" label and
 *                                     never fires onSelect
 *   t_login_landing_avoids_revoked  — if the saved active project is
 *                                     revoked, auto-switches to the next
 *                                     valid one + shows a notice toast
 *   t_add_repository_opens_connect_flow — the switcher menu offers a
 *                                   working Add repository entry
 *   t_add_repository_visible_with_no_projects — the entry shows even
 *                                   before any project is connected
 *   t_add_repository_default_navigates_to_connect_flow — without a
 *                                   handler the entry opens
 *                                   /projects?flow=connect-repo
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const get = vi.fn();
vi.mock("../../../../lib/api", () => ({ api: { get: (...args) => get(...args) } }));

const toast = vi.fn();
vi.mock("../../../Toast", () => ({ toast: (...args) => toast(...args) }));

import { ProjectSwitcher } from "../ProjectSwitcher.jsx";

const PROJECTS = [
  { project_id: "p_a", github_owner: "acme", github_repo: "web", name: "web" },
  { project_id: "p_b", github_owner: "acme", github_repo: "api", name: "api" },
];

beforeEach(() => {
  get.mockReset();
  toast.mockReset();
});

describe("ProjectSwitcher — R3 Repo Quick-Switch", () => {
  it("t_switch_repo_a_to_b: selecting a valid, reachable project calls onSelect with its id", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_a", status: "connected" },
      { project_id: "p_b", status: "connected" },
    ] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("project-switcher-trigger"));
    fireEvent.click(screen.getByTestId("project-switcher-item-p_b"));
    expect(onSelect).toHaveBeenCalledWith("p_b");
  });

  it("t_revoked_repo_non_selectable: a disconnected project is dimmed, labelled, and never selectable", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_a", status: "connected" },
      { project_id: "p_b", status: "disconnected" },
    ] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("project-switcher-trigger"));
    const row = await screen.findByTestId("project-switcher-item-p_b");
    expect(row).toHaveAttribute("data-disabled", "true");
    expect(row.textContent).toMatch(/repo unreachable/i);
    fireEvent.click(row);
    expect(onSelect).not.toHaveBeenCalled();
  });

  // H1 (X1/W1 hardening, 2026-08-30, overnight-loop-2 P0) — this test
  // used to lock the auto-switch behaviour; that behaviour is the
  // CONFIRMED root cause of the "active project silently switched
  // mid-session with no user action" incident (see
  // REPORT-x1-crossproject.md §W1) and has been removed by design.
  // Replaced with the two tests below that lock the fixed behaviour.
  it("t_disconnected_active_project_shows_notice_no_auto_switch: real revocation notifies but NEVER auto-switches", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_a", status: "disconnected" },
      { project_id: "p_b", status: "connected" },
    ] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(1));
    expect(onSelect).not.toHaveBeenCalled();
    expect(toast.mock.calls[0][0].message).toMatch(/disconnected/i);
    expect(toast.mock.calls[0][0].message).not.toMatch(/showing/i);
  });

  it("t_unreachable_active_project_never_switches_or_notifies: a TRANSIENT network blip is silent here (no switch, no toast) — exact regression guard for the reported incident", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_a", status: "unreachable" },
      { project_id: "p_b", status: "connected" },
    ] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 10));
    expect(onSelect).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
  });

  it("t_login_landing_noop_when_active_is_healthy: a reachable active project never triggers an auto-switch", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_a", status: "connected" },
      { project_id: "p_b", status: "connected" },
    ] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 10));
    expect(onSelect).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
  });

  it("t_r7_project_name_distinguishes_same_repo_projects: two projects on the same repo render with distinct names", async () => {
    get.mockResolvedValue({ data: { statuses: [
      { project_id: "p_x", status: "connected" },
      { project_id: "p_y", status: "connected" },
    ] } });
    const SAME_REPO_PROJECTS = [
      { project_id: "p_x", github_owner: "acme", github_repo: "monorepo", name: "Staging clone" },
      { project_id: "p_y", github_owner: "acme", github_repo: "monorepo", name: "Prod mirror" },
    ];
    render(<ProjectSwitcher projects={SAME_REPO_PROJECTS} activeProjectId="p_x" onSelect={vi.fn()} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("project-switcher-trigger"));
    const nameX = await screen.findByTestId("project-switcher-item-name-p_x");
    const nameY = await screen.findByTestId("project-switcher-item-name-p_y");
    expect(nameX.textContent).toBe("Staging clone");
    expect(nameY.textContent).toBe("Prod mirror");
    expect(nameX.textContent).not.toBe(nameY.textContent);
  });

  it("t_add_repository_opens_connect_flow: the switcher menu offers a working Add repository entry", async () => {
    get.mockResolvedValue({ data: { statuses: [] } });
    const onSelect = vi.fn();
    const onAddRepository = vi.fn();
    render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} onAddRepository={onAddRepository} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("project-switcher-trigger"));
    fireEvent.click(await screen.findByTestId("project-switcher-add-repository"));
    expect(onAddRepository).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("t_add_repository_visible_with_no_projects: the Add entry shows even before any project is connected", async () => {
    get.mockResolvedValue({ data: { statuses: [] } });
    const onSelect = vi.fn();
    render(<ProjectSwitcher projects={[]} activeProjectId={null} onSelect={onSelect} onAddRepository={vi.fn()} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("project-switcher-trigger"));
    expect(await screen.findByTestId("project-switcher-add-repository")).toBeInTheDocument();
    expect(screen.getByTestId("project-switcher-empty")).toBeInTheDocument();
  });

  it("t_add_repository_default_navigates_to_connect_flow: without a handler the entry opens /projects?flow=connect-repo", async () => {
    get.mockResolvedValue({ data: { statuses: [] } });
    const onSelect = vi.fn();
    const assign = vi.fn();
    const originalLocation = window.location;
    Object.defineProperty(window, "location", {
      value: { assign },
      configurable: true,
      writable: true,
    });
    try {
      render(<ProjectSwitcher projects={PROJECTS} activeProjectId="p_a" onSelect={onSelect} />);
      await waitFor(() => expect(get).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("project-switcher-trigger"));
      fireEvent.click(await screen.findByTestId("project-switcher-add-repository"));
      expect(assign).toHaveBeenCalledWith("/projects?flow=connect-repo");
    } finally {
      Object.defineProperty(window, "location", {
        value: originalLocation,
        configurable: true,
        writable: true,
      });
    }
  });
});
