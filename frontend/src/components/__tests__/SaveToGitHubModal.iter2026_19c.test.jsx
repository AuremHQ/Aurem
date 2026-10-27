/**
 * SaveToGitHubModal.iter2026_10h.test.jsx — follow-up regression for
 * the "Save to GitHub" modal showing a raw Cloudflare error page.
 *
 * Covers the frontend half of the 424-not-502 fix: structured
 * `{code, message, retryable}` detail is read correctly, raw infra
 * HTML/Cloudflare text is never rendered, a plain-string legacy
 * `detail` still works, a bare JS Error never leaks `e.message`, a
 * Retry button re-fetches repos/branches, and typing the base-branch
 * name into "Create new" flips `create_branch` to false.
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
      { owner: "acme", repo: "widgets", full_name: "acme/widgets", private: false, default_branch: "main" },
    ],
    default_owner: "acme", default_repo: "widgets",
  },
};

const BRANCHES_RESPONSE = {
  data: {
    ok: true, owner: "acme", repo: "widgets",
    branches: [{ name: "main", protected: true }, { name: "feature/old", protected: false }],
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

describe("SaveToGitHubModal — structured-error + Retry (iter2026_10h)", () => {
  it("renders the structured {code,message} detail's message, never '[object Object]'", async () => {
    const onConfirm = vi.fn().mockRejectedValue({
      response: {
        status: 424,
        data: { detail: { code: "github_timeout", message: "Couldn't reach GitHub in time — please try again.", retryable: true } },
      },
    });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    const err = await screen.findByTestId("save-to-github-error");
    expect(err.textContent).toContain("Couldn't reach GitHub in time");
    expect(err.textContent).not.toContain("[object Object]");
    expect(err.textContent).toContain("HTTP 424");
  });

  it("still shows a plain-string legacy detail unchanged", async () => {
    const onConfirm = vi.fn().mockRejectedValue({
      response: { status: 403, data: { detail: "That repo isn't in your connected GitHub access." } },
    });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    const err = await screen.findByTestId("save-to-github-error");
    expect(err.textContent).toContain("That repo isn't in your connected GitHub access.");
  });

  it("never renders raw Cloudflare/HTML infra text even if it slips through as a string detail", async () => {
    const onConfirm = vi.fn().mockRejectedValue({
      response: {
        status: 502,
        data: { detail: "<html><head><title>502 Bad Gateway</title></head><body>cloudflare</body></html>" },
      },
    });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    const err = await screen.findByTestId("save-to-github-error");
    expect(err.textContent.toLowerCase()).not.toContain("<html");
    expect(err.textContent.toLowerCase()).not.toContain("cloudflare");
    expect(err.textContent).toContain("unreadable response");
  });

  it("never leaks a raw JS Error message (e.g. 'Network Error') — generic fallback only", async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error("Network Error"));
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    const err = await screen.findByTestId("save-to-github-error");
    expect(err.textContent).toBe("Couldn't save to GitHub — please try again.");
  });

  it("repos-load failure shows Retry and a second click re-fetches", async () => {
    api.get.mockImplementation((url) => {
      if (url.includes("/github/repos")) {
        return Promise.reject({ response: { status: 424, data: { detail: { code: "github_timeout", message: "Couldn't reach GitHub in time — please try again.", retryable: true } } } });
      }
      return Promise.resolve({ data: {} });
    });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    await screen.findByTestId("save-to-github-modal");
    const err = await screen.findByTestId("save-to-github-repos-error");
    expect(err.textContent).toContain("Couldn't reach GitHub in time");

    api.get.mockImplementation((url) => {
      if (url.includes("/github/repos")) return Promise.resolve(REPOS_RESPONSE);
      return Promise.resolve({ data: {} });
    });
    fireEvent.click(screen.getByTestId("save-to-github-repos-retry"));
    await waitFor(() => expect(screen.getByTestId("save-to-github-repo-select")).toBeInTheDocument());
  });

  it("branches-load failure shows Retry and a second click re-fetches", async () => {
    let branchCallCount = 0;
    api.get.mockImplementation((url) => {
      if (url.includes("/github/branches")) {
        branchCallCount += 1;
        if (branchCallCount === 1) {
          return Promise.reject({ response: { status: 424, data: { detail: { code: "github_timeout", message: "Couldn't reach GitHub in time — please try again.", retryable: true } } } });
        }
        return Promise.resolve(BRANCHES_RESPONSE);
      }
      if (url.includes("/github/repos")) return Promise.resolve(REPOS_RESPONSE);
      return Promise.resolve({ data: {} });
    });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    await screen.findByTestId("save-to-github-modal");
    fireEvent.click(screen.getByTestId("save-to-github-tab-existing"));
    const err = await screen.findByTestId("save-to-github-branches-error");
    expect(err.textContent).toContain("Couldn't reach GitHub in time");

    fireEvent.click(screen.getByTestId("save-to-github-branches-retry"));
    await waitFor(() => expect(screen.getByTestId("save-to-github-existing-branch-select")).toBeInTheDocument());
    expect(branchCallCount).toBe(2);
  });

  it("typing the base branch name into 'Create new' sends create_branch:false (commit direct, no redundant branch-create call)", async () => {
    const onConfirm = vi.fn().mockResolvedValue({ task_id: "t1" });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    const input = screen.getByTestId("save-to-github-new-branch-input");
    fireEvent.change(input, { target: { value: "main" } });
    expect(await screen.findByTestId("save-to-github-main-warning")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("save-to-github-main-confirm-checkbox"));
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith({
      repo: "acme/widgets", branch: "main", create_branch: false, base_branch: "main",
    }));
  });

  it("a genuinely new branch name on 'Create new' still sends create_branch:true", async () => {
    const onConfirm = vi.fn().mockResolvedValue({ task_id: "t2" });
    render(<SaveToGitHubModal open activeProject={PROJECT} onCancel={vi.fn()} onConfirm={onConfirm} />);
    await screen.findByTestId("save-to-github-modal");
    const input = screen.getByTestId("save-to-github-new-branch-input");
    fireEvent.change(input, { target: { value: "auremcto/ship-fixed123" } });
    fireEvent.click(screen.getByTestId("save-to-github-save"));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith({
      repo: "acme/widgets", branch: "auremcto/ship-fixed123", create_branch: true, base_branch: "main",
    }));
  });
});
