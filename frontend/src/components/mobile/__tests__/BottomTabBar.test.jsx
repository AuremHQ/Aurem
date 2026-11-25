/**
 * BottomTabBar.test.jsx
 *
 * 2026-11-25 — Mobile nav fix: the bottom tab bar must be FULLY WIRED —
 * every tab navigates (calls onTabChange with its id) and the active tab
 * is highlighted. Phase 1 left Projects and Menu opening the desktop
 * sidebar drawer; Dashboard's handler now opens the bottom sheets
 * instead (that handler is a 10-line switch in Dashboard.jsx, reviewed
 * by inspection — this test proves the tab-bar side: each tab fires
 * exactly its own id, and the active state follows the prop).
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import BottomTabBar from "../BottomTabBar.jsx";

const TABS = ["chat", "projects", "ship", "menu"];

describe("BottomTabBar — every tab wired (2026-11-25 mobile nav fix)", () => {
  it("renders all four tabs with labels", () => {
    render(<BottomTabBar activeTab="chat" onTabChange={() => {}} />);
    for (const id of TABS) {
      expect(screen.getByTestId(`mobile-tab-${id}`)).toBeTruthy();
    }
    expect(screen.getByText("Chat")).toBeTruthy();
    expect(screen.getByText("Projects")).toBeTruthy();
    expect(screen.getByText("Ship")).toBeTruthy();
    expect(screen.getByText("Menu")).toBeTruthy();
  });

  it("clicking each tab calls onTabChange with exactly its id", () => {
    const onTabChange = vi.fn();
    render(<BottomTabBar activeTab="chat" onTabChange={onTabChange} />);
    for (const id of TABS) {
      fireEvent.click(screen.getByTestId(`mobile-tab-${id}`));
    }
    expect(onTabChange).toHaveBeenCalledTimes(4);
    expect(onTabChange).toHaveBeenNthCalledWith(1, "chat");
    expect(onTabChange).toHaveBeenNthCalledWith(2, "projects");
    expect(onTabChange).toHaveBeenNthCalledWith(3, "ship");
    expect(onTabChange).toHaveBeenNthCalledWith(4, "menu");
  });

  it("marks only the active tab is-active with aria-current", () => {
    render(<BottomTabBar activeTab="projects" onTabChange={() => {}} />);
    const active = screen.getByTestId("mobile-tab-projects");
    expect(active.className).toContain("is-active");
    expect(active.getAttribute("aria-current")).toBe("page");
    for (const id of ["chat", "ship", "menu"]) {
      const tab = screen.getByTestId(`mobile-tab-${id}`);
      expect(tab.className).not.toContain("is-active");
      expect(tab.getAttribute("aria-current")).toBeNull();
    }
  });

  it("renders in the tab bar nav (visible while the keyboard is closed)", () => {
    render(<BottomTabBar activeTab="ship" onTabChange={() => {}} />);
    const bar = screen.getByTestId("mobile-tab-bar");
    expect(bar.tagName).toBe("NAV");
    expect(bar.style.display).not.toBe("none");
  });
});
