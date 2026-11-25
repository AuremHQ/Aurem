/**
 * TopBar.test.jsx
 *
 * 2026-11-25 — Mobile nav fix: the gold O logo MUST stay visible in the
 * mobile view. The desktop rail carried it and the rail is hidden on
 * mobile now, so the logo moves into the top bar (compact brand mark,
 * mobile only at <=900px via the CSS block — jsdom cannot apply media
 * queries, so this test proves the logo renders with the right testid /
 * class and fires onLogoClick; the <=900px show / >900px hide is the
 * same plain-CSS pattern BottomTabBar's media query uses, verified by
 * inspection of the CSS string).
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { TopBar } from "../TopBar.jsx";

function renderTopBar(props = {}) {
  return render(
    <TopBar
      tab="Chat"
      onTabChange={() => {}}
      onNewRun={() => {}}
      hasRepo={false}
      {...props}
    />,
  );
}

describe("TopBar — mobile logo (2026-11-25 mobile nav fix)", () => {
  it("renders the logo button when onLogoClick is passed and fires it on click", () => {
    const onLogoClick = vi.fn();
    renderTopBar({ onLogoClick });
    const logo = screen.getByTestId("ds2-topbar-logo");
    expect(logo.className).toContain("ds2-topbar-logo");
    expect(logo.querySelector("img").getAttribute("alt")).toBe("AUREM");
    fireEvent.click(logo);
    expect(onLogoClick).toHaveBeenCalledTimes(1);
  });

  it("renders no logo when onLogoClick is not passed (other TopBar users unaffected)", () => {
    renderTopBar();
    expect(screen.queryByTestId("ds2-topbar-logo")).toBeNull();
  });

  it("marks the New run button and streak slot with their mobile-hide classes", () => {
    renderTopBar({
      hasRepo: true,
      streakSlot: <span data-testid="streak-stub">streak</span>,
    });
    expect(screen.getByTestId("ds2-new-run").className).toContain("ds2-topbar-newrun");
    expect(screen.getByTestId("streak-stub").closest(".ds2-topbar-streak")).toBeTruthy();
  });

  it("embeds the <=900px mobile rules (logo show, streak/health-label/new-run hide)", () => {
    const { container } = renderTopBar({ onLogoClick: () => {} });
    const css = container.querySelector("style")?.textContent || "";
    expect(css).toContain("@media (max-width: 900px)");
    expect(css).toContain(".ds2-topbar-logo { display: flex; }");
    expect(css).toContain(".ds2-topbar-streak");
    expect(css).toContain(".ds2-topbar-health-label");
    expect(css).toContain(".ds2-topbar-newrun");
  });
});
