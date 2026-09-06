/**
 * VisibilityKitPanel.applyOkFalse.test.jsx — 2026-09-12 live-production
 * bug fix (TJSNDHU/Aurem, founder-verified on real repo).
 *
 * Bug: backend POST /visibility/projects/{id}/apply returns HTTP 200
 * even when nothing was applied (e.g. a conflict — robots.txt already
 * exists without our managed-block marker: `{ok: false, error:
 * "all_conflicted", conflicts: [...]}`). Axios never throws for a 200
 * response, so the frontend's try/catch never saw this as a failure —
 * it blindly called toast.success() and closed the dialog, leaving the
 * founder with zero feedback and the item still stuck on "Ready".
 *
 * Fix: check `data.ok` explicitly before declaring success.
 *
 * Named tests:
 *   t_apply_ok_false_shows_real_reason_not_false_success
 *   t_apply_ok_true_shows_success_and_reloads
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

const get = vi.fn();
const post = vi.fn();
vi.mock("../../lib/api", () => ({ api: { get: (...a) => get(...a), post: (...a) => post(...a) } }));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({ toast: { error: (...a) => toastError(...a), success: (...a) => toastSuccess(...a) } }));

import VisibilityKitPanel from "../VisibilityKitPanel.jsx";

const STATE_WITH_READY_ROBOTS = {
  apply_enabled: true,
  apply_disabled_reason: null,
  score: 12,
  items: [
    {
      key: "ai_crawler_policy", name: "robots.txt AI policy", mode: "auto",
      status: "missing", what_why: "Lets AI crawlers know your policy.",
    },
  ],
};

beforeEach(() => {
  get.mockReset(); post.mockReset(); toastError.mockReset(); toastSuccess.mockReset();
  get.mockResolvedValue({ data: STATE_WITH_READY_ROBOTS });
});

async function openConfirmModalForRobots() {
  render(<VisibilityKitPanel projectId="p_test" siteDomain="example-real.com" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByTestId("kit-apply-row-ai_crawler_policy")).toBeInTheDocument());
  fireEvent.click(screen.getByTestId("kit-apply-row-ai_crawler_policy"));
  await waitFor(() => expect(screen.getByTestId("kit-apply-confirm-modal")).toBeInTheDocument());
}

describe("VisibilityKitPanel — apply ok:false handling (2026-09-12 fix)", () => {
  it("t_apply_ok_false_shows_real_reason_not_false_success: conflict must surface as an error, never a false success", async () => {
    post.mockResolvedValue({
      data: { ok: false, error: "all_conflicted", conflicts: ["robots.txt already exists without an AUREM managed block"] },
    });
    await openConfirmModalForRobots();
    fireEvent.click(screen.getByTestId("kit-apply-confirm-submit"));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError.mock.calls[0][0]).toMatch(/robots\.txt already exists/);
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("t_apply_ok_true_shows_success_and_reloads: a genuine success still shows success + reloads state", async () => {
    post.mockResolvedValue({ data: { ok: true, pr_url: "https://github.com/TJSNDHU/Aurem/pull/999" } });
    await openConfirmModalForRobots();
    fireEvent.click(screen.getByTestId("kit-apply-confirm-submit"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess.mock.calls[0][0]).toMatch(/pull\/999/);
    expect(toastError).not.toHaveBeenCalled();
    // reload state after a real success (load() is called again -> 2nd GET)
    await waitFor(() => expect(get.mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});
