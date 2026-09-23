import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const mocks = vi.hoisted(() => ({ navigate: vi.fn(), active: vi.fn(), connection: {} }));
vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("../../lib/api", () => ({ api: { post: vi.fn() } }));
vi.mock("../Toast", () => ({ toast: vi.fn() }));
vi.mock("../TabBar", () => ({ setActiveProjectId: mocks.active }));
vi.mock("../../lib/analytics", () => ({ metaLead: vi.fn(), openaiGithubRepoConnected: vi.fn() }));
vi.mock("../../hooks/useGitHubConnectStatus", () => ({ default: () => mocks.connection }));
import AddProjectWizard from "../AddProjectWizard";
import { api } from "../../lib/api";

const repo = { full_name: "team/new-repo", default_branch: "develop", private: true };
beforeEach(() => {
  vi.clearAllMocks();
  mocks.connection = {
    status: { installations: [{ installation_id: 10, github_login: "team", repositories: [repo] }] },
    freshInstallIds: ["10"], startConnect: vi.fn(() => ({ ok: true })), refresh: vi.fn(),
  };
  api.post.mockResolvedValue({ data: { ok: true, project_id: "p_new" } });
});

describe("Add Project persistence handoff", () => {
  it("saves the selected repo/default branch and stays on Projects after refresh", async () => {
    const onAdded = vi.fn().mockResolvedValue(undefined);
    render(<AddProjectWizard stayOnProjects onClose={vi.fn()} onAdded={onAdded} />);
    const save = await screen.findByTestId("wizard-save");
    expect(api.post).not.toHaveBeenCalled(); // installation is not project creation
    fireEvent.click(save);
    await waitFor(() => expect(onAdded).toHaveBeenCalledWith("p_new"));
    expect(api.post).toHaveBeenCalledWith("/cto/projects/add", {
      name: "new-repo", github_url: "https://github.com/team/new-repo",
      installation_id: 10, branch: "develop",
    });
    expect(mocks.active).toHaveBeenCalledWith("p_new");
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("recovers authorized repos after reload without automatically selecting stale history", async () => {
    mocks.connection.freshInstallIds = [];
    render(<AddProjectWizard stayOnProjects onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.queryByTestId("wizard-step-2")).toBeNull();
    expect(screen.queryByTestId("add-wizard-repo-team/new-repo")).toBeNull();
    fireEvent.click(screen.getByTestId("add-wizard-use-existing-access"));
    expect(screen.queryByTestId("wizard-step-2")).toBeNull();
    fireEvent.click(screen.getByTestId("add-wizard-repo-team/new-repo"));
    fireEvent.click(await screen.findByTestId("wizard-save"));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
  });

  it("does not report success if the server returns no project ID", async () => {
    api.post.mockResolvedValue({ data: { ok: true } });
    const onAdded = vi.fn();
    render(<AddProjectWizard onClose={vi.fn()} onAdded={onAdded} />);
    fireEvent.click(await screen.findByTestId("wizard-save"));
    expect(await screen.findByTestId("add-wizard-save-error")).toHaveTextContent("not confirmed");
    expect(onAdded).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("surfaces a project-creation route error without losing selection", async () => {
    api.post.mockRejectedValue({ response: { data: { detail: { message: "Repository access is still syncing." } } } });
    render(<AddProjectWizard onClose={vi.fn()} onAdded={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("wizard-save"));
    expect(await screen.findByTestId("add-wizard-save-error")).toHaveTextContent("still syncing");
    expect(screen.getByTestId("project-name-input")).toHaveValue("new-repo");
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("opens an already-persisted project after 409 without a second create", async () => {
    api.post.mockRejectedValue({ response: { status: 409, data: { detail: {
      error: "already_connected", message: "Already your project.", project_id: "p_existing",
    } } } });
    const onAdded = vi.fn();
    render(<AddProjectWizard stayOnProjects onClose={vi.fn()} onAdded={onAdded} />);
    fireEvent.click(await screen.findByTestId("wizard-save"));
    fireEvent.click(await screen.findByTestId("add-wizard-open-existing"));
    await waitFor(() => expect(onAdded).toHaveBeenCalledWith("p_existing"));
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("retains the saved ID if list refresh fails, allowing refresh without duplicate creation", async () => {
    const onAdded = vi.fn().mockRejectedValueOnce(new Error("network")).mockResolvedValue(undefined);
    render(<AddProjectWizard stayOnProjects onClose={vi.fn()} onAdded={onAdded} />);
    fireEvent.click(await screen.findByTestId("wizard-save"));
    expect(await screen.findByTestId("add-wizard-save-error")).toHaveTextContent("Project saved");
    fireEvent.click(screen.getByTestId("add-wizard-open-existing"));
    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(2));
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("blocks duplicate saves and closing the wizard while creation is in flight", async () => {
    let resolve;
    api.post.mockReturnValue(new Promise((r) => { resolve = r; }));
    const onClose = vi.fn();
    render(<AddProjectWizard stayOnProjects onClose={onClose} onAdded={vi.fn()} />);
    const save = await screen.findByTestId("wizard-save");
    fireEvent.click(save); fireEvent.click(save);
    fireEvent.click(screen.getByTestId("add-project-wizard-backdrop"));
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId("add-project-close")).toBeDisabled();
    resolve({ data: { project_id: "p_new" } });
    await waitFor(() => expect(save).not.toBeDisabled());
  });
});