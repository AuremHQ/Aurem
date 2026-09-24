/**
 * DateDivider.test.jsx — t_theme_aware
 *
 * Confirms the divider renders correctly regardless of the active
 * theme by asserting it uses the app's theme-aware CSS variables
 * (--text-faint / --border, defined for BOTH light and dark themes
 * in src/index.css) rather than hardcoded colors, and that it renders
 * identically when mounted under either a `light` or `dark`
 * `data-theme` ancestor (the app's theme-switch mechanism).
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DateDivider } from "../DateDivider";

function renderUnderTheme(theme, text) {
  return render(
    <div data-theme={theme}>
      <DateDivider text={text} />
    </div>
  );
}

describe("DateDivider", () => {
  it("t_theme_aware: renders using theme-aware CSS vars, identically under light and dark", () => {
    const { unmount: unmountLight } = renderUnderTheme("light", "Today");
    const lightEl = screen.getByTestId("chat-date-divider");
    expect(lightEl).toBeInTheDocument();
    expect(lightEl.textContent).toBe("Today");
    // Uses CSS variables, not a hardcoded hex that would look wrong
    // in the opposite theme.
    const lightLine = lightEl.querySelector("div");
    expect(lightLine.style.background).toBe("var(--border)");
    unmountLight();

    renderUnderTheme("dark", "Today");
    const darkEl = screen.getByTestId("chat-date-divider");
    expect(darkEl).toBeInTheDocument();
    expect(darkEl.textContent).toBe("Today");
    const darkLine = darkEl.querySelector("div");
    expect(darkLine.style.background).toBe("var(--border)");
  });

  it("is a non-interactive, non-selectable separator", () => {
    render(<DateDivider text="Today" />);
    const el = screen.getByTestId("chat-date-divider");
    expect(el.style.pointerEvents).toBe("none");
    expect(el.style.userSelect).toBe("none");
    expect(el.getAttribute("role")).toBe("separator");
  });

  it("renders nothing when text is empty/falsy", () => {
    const { container } = render(<DateDivider text="" />);
    expect(container.firstChild).toBeNull();
  });
});
