/**
 * ToolsPage.liveLinks.test.jsx — UI-fix C1 (2026-10)
 *
 * Bug fix: Bug Hunt and Health Scan used to render a disabled
 * "Coming soon" button even though /bug-hunt and /codebase-health
 * already exist and work. Regression-guard that those two cards now
 * link straight there, and that the embedded-only tools (Vanguard,
 * Security Scan) render no repo picker / no CTA button.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import ToolsPage from "../ToolsPage";

describe("ToolsPage live tool links", () => {
  it("links the Bug Hunt card straight to /bug-hunt", () => {
    render(<ToolsPage />);
    const link = screen.getByTestId("tools-card-bug-hunt-cta");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/bug-hunt");
  });

  it("links the Health Scan card straight to /codebase-health", () => {
    render(<ToolsPage />);
    const link = screen.getByTestId("tools-card-health-scan-cta");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/codebase-health");
  });

  it("shows an honest embedded-tool line and no CTA/repo-picker for Vanguard + Security Scan", () => {
    render(<ToolsPage />);
    expect(screen.queryByTestId("tools-card-vanguard-cta")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tools-card-security-scan-cta")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Runs inside ORA chat and CI/i).length).toBeGreaterThan(0);
  });
});
