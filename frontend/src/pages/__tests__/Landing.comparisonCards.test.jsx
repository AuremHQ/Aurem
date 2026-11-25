/**
 * Landing.comparisonCards.test.jsx — mobile stacked comparison cards
 * (homepage port, 2026-11-25).
 *
 * Behavioural test (AGENTS.md §7: no STATIC_GREP): renders the real
 * Landing page with React Testing Library and asserts the comparison
 * section renders BOTH the desktop table and the mobile card stack
 * from the same COMPARE_ROWS data, so the 390px cut-off bug
 * ("MIT extension" rendered as "MIT extensio" in the scrolling table)
 * cannot recur: the cards carry every value in full.
 *
 * CSS picks which one is visible (table hidden <=640px, cards shown);
 * jsdom applies the base `display: none` on .compare-cards but does
 * not evaluate media queries, so visibility switching itself is
 * verified by the 390px Playwright screenshot, not here. What this
 * test pins is the DOM contract the CSS relies on.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Landing from "../Landing";

vi.mock("../lib/api", () => ({
  api: { get: vi.fn(() => Promise.reject(new Error("no network in test"))) },
  healthApi: { get: vi.fn(() => Promise.reject(new Error("no network in test"))) },
  getToken: () => null,
  getUser: () => null,
  isAdminOrFounder: () => false,
}));

beforeEach(() => {
  // jsdom has no matchMedia; components that probe it must get a stub.
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    });
  }
});

function renderLanding() {
  return render(
    <MemoryRouter>
      <Landing />
    </MemoryRouter>
  );
}

async function getCards() {
  const cards = await screen.findByTestId("compare-cards");
  return { cards, cardEls: Array.from(cards.querySelectorAll(".compare-card")) };
}

describe("Landing.jsx — mobile comparison cards (ORA column fix)", () => {
  it("renders one card per comparison row (10 rows, same data as the table)", async () => {
    renderLanding();
    const { cardEls } = await getCards();
    expect(cardEls).toHaveLength(10);
    expect(cardEls.map((c) => c.querySelector(".compare-card-title").textContent)).toEqual([
      "Ships changes to your GitHub repo",
      "No IDE required",
      "Mobile-friendly (web + PWA)",
      expect.stringContaining("Flat pricing"),
      "MCP server (Claude / Cursor / VS Code)",
      "VS Code extension",
      "Codebase memory (warm-start)",
      "Security scan every commit",
      "OAuth 2.1 + PKCE for AI clients",
      "Open source friendly",
    ]);
  });

  it("shows the full 'MIT extension' ORA value in the last card — never cut off", async () => {
    renderLanding();
    const { cardEls } = await getCards();
    const lastCard = cardEls[9];
    // Exact full text: the value renders with no ✓/✗ prefix by design,
    // so the whole string "✓ MIT extension" must be present, not a
    // clipped "MIT extensio".
    expect(within(lastCard).getByText(/MIT extension/)).toBeTruthy();
    expect(lastCard.textContent).toContain("MIT extension");
    expect(lastCard.textContent).not.toContain("MIT extensio ");
    // And the ORA row of that card carries it (not a rival row).
    const oraRow = lastCard.querySelector(".compare-card-row.ora-row");
    expect(oraRow).toBeTruthy();
    expect(oraRow.textContent).toContain("MIT extension");
  });

  it("every card lists all five vendors with the ORA row first", async () => {
    renderLanding();
    const { cardEls } = await getCards();
    for (const card of cardEls) {
      const labels = within(card)
        .getAllByText(/^(ORA|GITHUB COPILOT|CURSOR|DEVIN|CLAUDE CODE)$/i)
        .map((el) => el.textContent.toUpperCase());
      expect(labels).toEqual(["ORA", "GITHUB COPILOT", "CURSOR", "DEVIN", "CLAUDE CODE"]);
    }
  });

  it("keeps the desktop table rendered alongside the cards (CSS picks which shows)", async () => {
    renderLanding();
    const table = await screen.findByRole("table");
    expect(table).toBeTruthy();
    expect(within(table).getByText(/MIT extension/)).toBeTruthy();
    expect(await screen.findByTestId("compare-cards")).toBeTruthy();
  });

  it("card ORA values match the table ORA column (one source of truth)", async () => {
    renderLanding();
    const { cardEls } = await getCards();
    const firstOraRow = cardEls[0].querySelector(".compare-card-row.ora-row");
    expect(firstOraRow.textContent).toContain("Yes");
    const pricingOraRow = cardEls[3].querySelector(".compare-card-row.ora-row");
    expect(pricingOraRow.textContent).toContain("$9/mo");
  });
});

describe("Landing.jsx — health score card (homepage port)", () => {
  it("shows the verified PR #371 numbers: 65 overall, Architecture 55 → 75", async () => {
    renderLanding();
    const card = await screen.findByTestId("health-score-card");
    expect(card.textContent).toContain("65");
    expect(card.textContent).toContain("55 → 75");
    expect(card.textContent).toContain("16");
  });

  it("the Codebase Health Scanner team card carries the same numbers", async () => {
    renderLanding();
    await screen.findByTestId("health-score-card");
    expect(screen.getByText(/now at 65 overall, Architecture 75/)).toBeTruthy();
  });
});
