import { renderHook, act } from "@testing-library/react";
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  API_BASE: "https://api.test", getToken: () => "test-token",
}));
vi.mock("../../lib/githubFunnel", () => ({ trackFunnel: vi.fn() }));
import useGitHubConnectStatus from "../useGitHubConnectStatus";
import { api } from "../../lib/api";

const installation = (id, names) => ({
  installation_id: id, github_login: `org-${id}`,
  repositories: names.map((full_name) => ({ full_name, default_branch: "develop" })),
});
const status = (...installations) => ({ data: {
  installation_active: installations.length > 0, installations,
  state: installations.some((i) => i.repositories.length) ? "connected" : "pending",
} });
let popup;
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  popup = { closed: false, close: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(popup);
  api.post.mockResolvedValue({ data: { ok: true } });
});
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

async function callback(id) {
  await act(async () => {
    const event = new MessageEvent("message", {
      origin: "https://api.test",
      data: { type: "aurem-app-installed", status: "success", install_id: id },
    });
    Object.defineProperty(event, "source", { value: popup });
    window.dispatchEvent(event);
  });
}

describe("project handoff regressions", () => {
  it("finishes a confirmed reconnect even when the repo grant is unchanged", async () => {
    api.get.mockResolvedValue(status(installation(10, ["org/one", "org/two"])));
    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => {});
    act(() => { result.current.startConnect(); });
    await callback(10);
    expect(result.current.connecting).toBe(false);
    popup.closed = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(result.current.denied).toBe(false);
    expect(result.current.freshInstallIds).toEqual(["10"]);
  });

  it("detects repo replacement when the number of repositories is unchanged", async () => {
    api.get.mockResolvedValue(status(installation(10, ["org/old"])));
    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => {});
    act(() => { result.current.startConnect(); });
    api.get.mockResolvedValue(status(installation(10, ["org/new"])));
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    expect(result.current.freshInstallIds).toEqual(["10"]);
    expect(result.current.connecting).toBe(false);
  });

  it("continues syncing the new installation despite an unrelated connected repo", async () => {
    const old = installation(1, ["old/repo"]);
    api.get.mockResolvedValue(status(old));
    const { result } = renderHook(() => useGitHubConnectStatus());
    await act(async () => {});
    act(() => { result.current.startConnect(); });
    api.get.mockResolvedValue(status(old, installation(2, [])));
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    api.get.mockResolvedValue(status(old, installation(2, ["new/repo"])));
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(result.current.status.installations[1].repositories).toHaveLength(1);
    expect(result.current.freshInstallIds).toEqual(["2"]);
  });

  it("ignores bridge messages from an untrusted origin", async () => {
    api.get.mockResolvedValue(status());
    renderHook(() => useGitHubConnectStatus());
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(new MessageEvent("message", {
        origin: "https://untrusted.example",
        data: { type: "aurem-app-installed", status: "success", install_id: 22 },
      }));
    });
    expect(api.post).not.toHaveBeenCalled();
  });
});