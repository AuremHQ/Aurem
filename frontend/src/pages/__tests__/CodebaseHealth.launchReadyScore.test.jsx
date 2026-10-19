/**
 * CodebaseHealth.launchReadyScore.test.jsx — UI-fix B3 (2026-10)
 * "LAUNCH-READY 9/9" used to compare scanned categories against
 * themselves (looks like 100% even with only 9 of the real 16
 * categories scanned). Must show the honest "X of 16 checked" unless
 * ALL 16 ran AND nothing critical/high is open.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { HealthBadge } from "../CodebaseHealth.jsx";

const nine = Object.fromEntries(
  Array.from({ length: 9 }, (_, i) => [`cat${i}`, { total: 0, score: 100 }]),
);
const sixteen = Object.fromEntries(
  Array.from({ length: 16 }, (_, i) => [`cat${i}`, { total: 0, score: 100 }]),
);

describe("UI-fix B3 · HealthBadge launch-ready score", () => {
  it("shows '9 of 16 checked' and NOT 'Launch-ready' when only 9 of 16 categories were scanned", () => {
    render(<HealthBadge score={88} label="Healthy" tone="#4ade80" breakdown={nine} criticalHigh={0} />);
    const el = screen.getByTestId("launch-ready-score");
    expect(el).toHaveTextContent("9 of 16 checked");
    expect(el).not.toHaveTextContent("Launch-ready");
  });

  it("shows 'Launch-ready' only when all 16 categories were scanned and nothing critical/high is open", () => {
    render(<HealthBadge score={95} label="Healthy" tone="#4ade80" breakdown={sixteen} criticalHigh={0} />);
    const el = screen.getByTestId("launch-ready-score");
    expect(el).toHaveTextContent("Launch-ready");
    expect(el).toHaveTextContent("16 / 16");
  });

  it("falls back to 'X of 16 checked' when all 16 ran but a critical/high finding is still open", () => {
    render(<HealthBadge score={60} label="Needs work" tone="#f87171" breakdown={sixteen} criticalHigh={2} />);
    const el = screen.getByTestId("launch-ready-score");
    expect(el).toHaveTextContent("16 of 16 checked");
    expect(el).not.toHaveTextContent("Launch-ready");
  });
});
