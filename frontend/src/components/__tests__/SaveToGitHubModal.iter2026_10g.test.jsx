/**
 * SaveToGitHubModal.iter2026_10g.test.jsx — Part B regression.
 *
 * Founder-reported 2026-10-21 incident: "Approve the fix" pushed
 * straight to main with no branch choice, no warning. This modal is
 * the fix — nothing writes to GitHub until the user explicitly
 * confirms a repo/branch here.
 */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
import { api } from "../../lib/api";
import SaveToGitHubModal from "../SaveToGitHubModal";

const PROJECT = {
  project_id: "p1", github_owner: "acme", github_repo: "widgets", branch: "main",
};

const REPOS_RESPONSE = {
  data: {
    ok: true,
    repos: [
      { owner: "acme", repo: "widgets", full_name: "acme/widgets", private: false, default_branch: "main", can_push: true },
      { owner: "acme", repo: "other", full_name: "acme/other", private: true, default_branch: "main", can_push: true },
    ],
    default_owner: "acme", default_repo: "widgets",
  },
};

const BRANCHES_RESPONSE = {
  data: {
    ok: true, owner: "acme", repo: "widgets",
    branches: [
      { name: "main", protected: true },
      { name: "feature/old-work", protected: false },
    ],
    truncated: false,
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockImplementation((url) => {
    if (url.includes("/github/branches")) return Promise.resolve(BRANCHES_RESPONSE);
    if (url.includes("/github/repos")) return Promise.resolve(REPOS_RESPONSE);
    return Promise.resolve({ data: {} });
  });
});

describe("SaveToGitHubModal — Part B", () => {
  it("does not render anything when closed, and fetches repos only once opened", async () => {
    const { rerender } = render(
      <SaveToGitHubModal open={false} activeProject={PROJECT} onCancel={vi.fn()} onConfirm={vi.fn()} />,
    );
    expect(screen.queryByTestId("save-to-github-modal")).not.toBeInTheDocument();
    expect(api.get).not.toHaveBeenCalled();

    rerender(
      <SaveToGitHubModal open onCancel={vi.fn()} onConfirm={vi.fn()} activeProject={PROJECT} />,
    );
    expect(await screen.findByTestId("save-to-github-modal")).toBeInTheDocument();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(
      "/cto/projects/p1/github/repos",
    ));
  });

  it("defaults to the 'Create new' tab with a non-main branch name — never main by default", async () => {
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    await screen.findByTestId("save-to-github-modal");
    expect(screen.getByTestId("save-to-github-tab-create")).toBeInTheDocument();
    const input = screen.getByTestId("save-to-github-new-branch-input");
    expect(input.value).toMatch(/^auremcto\/ship-/);
    expect(input.value).not.toBe("main");
    expect(screen.queryByTestId("save-to-github-main-warning")).not.toBeInTheDocument();
  });

  it("makes NO write call before the user clicks 'Save to GitHub'", async () => {
    const onConfirm = vi.fn().mockResolvedValue({ task_id: "t1" });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    // Switch tabs, search branches, open the repo dropdown — none of
    // this should trigger onConfirm (the real /cto/tasks/submit
    // write lives behind that prop in the real app).
    fireEvent.click(screen.getByTestId("save-to-github-tab-existing"));
    await screen.findByTestId("save-to-github-existing-branch-select");
    fireEvent.change(screen.getByTestId("save-to-github-branch-search"), {
      target: { value: "feature" },
    });
    expect(onConfirm).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("picking the repo's default branch on 'Existing branch' shows the main warning and blocks Save until the checkbox is checked", async () => {
    const onConfirm = vi.fn().mockResolvedValue({ task_id: "t1" });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-tab-existing"));
    const select = await screen.findByTestId("save-to-github-existing-branch-select");
    fireEvent.change(select, { target: { value: "main" } });

    expect(await screen.findByTestId("save-to-github-main-warning")).toBeInTheDocument();
    expect(screen.getByTestId("save-to-github-save")).toBeDisabled();

    fireEvent.click(screen.getByTestId("save-to-github-main-confirm-checkbox"));
    expect(screen.getByTestId("save-to-github-save")).not.toBeDisabled();

    fireEvent.click(screen.getByTestId("save-to-github-save"));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith({
      repo: "acme/widgets", branch: "main", create_branch: false, base_branch: null,
    }));
  });

  it("the default 'Existing branch' auto-selection never lands on the default/main branch", async () => {
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-tab-existing"));
    const select = await screen.findByTestId("save-to-github-existing-branch-select");
    await waitFor(() => expect(select.value).toBe("feature/old-work"));
    expect(screen.queryByTestId("save-to-github-main-warning")).not.toBeInTheDocument();
  });

  it("shows a true success screen with the chosen repo/branch after a successful confirm", async () => {
    const onConfirm = vi.fn().mockResolvedValue({ task_id: "t99" });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    const success = await screen.findByTestId("save-to-github-success");
    expect(success.textContent).toContain("acme/widgets");
    // "Create new" branch — not browsable yet, must NOT claim a fake link.
    expect(screen.queryByTestId("save-to-github-view-link")).not.toBeInTheDocument();
    expect(success.textContent.toLowerCase()).not.toContain("live on main");
    expect(success.textContent.toLowerCase()).not.toContain("deployed");
  });

  it("existing-branch success screen DOES show a real 'View branch' link (branch already exists)", async () => {
    const onConfirm = vi.fn().mockResolvedValue({ task_id: "t100" });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-tab-existing"));
    const select = await screen.findByTestId("save-to-github-existing-branch-select");
    await waitFor(() => expect(select.value).toBe("feature/old-work"));
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    await screen.findByTestId("save-to-github-success");
    const link = screen.getByTestId("save-to-github-view-link");
    expect(link).toHaveAttribute(
      "href", "https://github.com/acme/widgets/tree/feature%2Fold-work",
    );
  });

  it("shows a human error message (never a raw exception string) when the save fails", async () => {
    const onConfirm = vi.fn().mockRejectedValue({
      response: { data: { detail: "That repo isn't in your connected GitHub access." } },
    });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    const err = await screen.findByTestId("save-to-github-error");
    expect(err.textContent).toBe("That repo isn't in your connected GitHub access.");
    expect(err.textContent).not.toMatch(/Error:|Traceback|at Object\./);
  });

  it("falls back to a generic human message when the backend sends no detail at all", async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error("Network Error"));
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    const err = await screen.findByTestId("save-to-github-error");
    expect(err.textContent).toBe("Couldn't save to GitHub — please try again.");
  });

  it("Cancel closes without ever calling onConfirm", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={onCancel} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-cancel"));
    expect(onCancel).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
  });
});
