/**
 * UsageLimitModal.tokenWording.test.jsx — Master Prompt v2 PART 5.2.
 * Legacy wording today; token wording when billingCopy reports
 * TOKEN_WORDING_ACTIVE (mocked).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("../../../lib/api", () => ({ api: { post: vi.fn() } }));

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
});

describe("UsageLimitModal — legacy mode (real billingCopy)", () => {
  it("shows the old task-count heading and plan cards", async () => {
    const { default: UsageLimitModal } = await import("../UsageLimitModal.jsx");
    render(
      <UsageLimitModal
        open={true}
        onClose={() => {}}
        usage={{ monthly_task_cap: 10, tasks_this_month: 10 }}
      />,
    );
    expect(screen.getByText(/You've used all 10 free tasks this month/)).toBeInTheDocument();
    expect(screen.getByText("50 tasks/month")).toBeInTheDocument();
  });
});

describe("UsageLimitModal — token-mode wording (billingCopy mocked)", () => {
  it("shows token wording instead of task counts", async () => {
    vi.doMock("../../../lib/billingCopy", () => ({
      TOKEN_WORDING_ACTIVE: true,
      tierCapacityLine: (tierId) => ({ starter: "900 tokens / month", pro: "1,900 tokens / month" }[tierId] || null),
    }));
    const { default: UsageLimitModal } = await import("../UsageLimitModal.jsx");
    render(
      <UsageLimitModal
        open={true}
        onClose={() => {}}
        usage={{ monthly_task_cap: 10, tasks_this_month: 10 }}
      />,
    );
    expect(screen.getByText(/You're out of tokens this month/)).toBeInTheDocument();
    expect(screen.queryByText(/free tasks this month/)).toBeNull();
    expect(screen.getByText("900 tokens / month")).toBeInTheDocument();
    expect(screen.queryByText("50 tasks/month")).toBeNull();
  });
});
