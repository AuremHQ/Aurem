/**
 * OraGuideMascot.robotFixList.test.jsx
 * Robot Fix List — FE1/FE2/FE3/F3 regression tests.
 *
 * FE1: project A->B switch; A's slower /findings/fix-list response
 *      must NOT land after B's and overwrite it.
 * FE2: items clear the instant the project changes (never show a
 *      stale project's list), and a failed fetch shows an explicit
 *      retry state instead of a silent no-op.
 * FE3: the `aurem:finding-fixed` window event is ignored when its
 *      project_id doesn't match the active project, and the open
 *      count is never decremented for an id that wasn't in the list.
 * F3:  the "see all" panel groups issues by file with a "Fix this
 *      file" action.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
vi.mock("../../lib/api", () => ({
  api: { get: (...args) => apiGetMock(...args), post: (...args) => apiPostMock(...args) },
}));
vi.mock("../GlobalHelpFAB", () => ({ shouldHide: () => false }));
vi.mock("../Toast", () => ({ toast: vi.fn() }));
vi.mock("../../hooks/useGuideSpotlight.jsx", () => ({
  useGuideSpotlight: () => {}, GuideSpotlightStyle: () => null,
}));
vi.mock("../../hooks/useOverlaySlot", () => ({ useCanShowOverlay: () => true }));

// A real external-store-style mock so changing the "active project"
// from the test causes the ALREADY-MOUNTED OraGuideMascot to
// re-render with the new value, exactly like a real project switch.
let _activeProject = { project_id: "proj-A" };
const _subscribers = new Set();
vi.mock("../TabBar", () => ({
  useActiveProject: () => {
    const [, setTick] = React.useState(0);
    React.useEffect(() => {
      const fn = () => setTick((t) => t + 1);
      _subscribers.add(fn);
      return () => _subscribers.delete(fn);
    }, []);
    return _activeProject;
  },
  setActiveProjectId: vi.fn(),
}));
function setActiveProjectForTest(project) {
  _activeProject = project;
  _subscribers.forEach((fn) => fn());
}

import OraGuideMascot from "../OraGuideMascot";

function Harness() {
  return (
    <MemoryRouter initialEntries={["/dashboard"]}>
      <OraGuideMascot />
    </MemoryRouter>
  );
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("Robot Fix List — FE1/FE2/FE3/F3", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem("aurem_token", "tok");
    setActiveProjectForTest({ project_id: "proj-A" });
  });

  it("FE1 — a late response for the OLD project never overwrites the new project's list", async () => {
    const deferredA = deferred();
    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.project_id === "proj-A") return deferredA.promise;
      if (opts?.params?.project_id === "proj-B") {
        return Promise.resolve({
          data: {
            items: [{ finding_id: "b1", severity: "high", title: "B issue", file: "b.py" }],
            total_open: 1, files: [{ file: "b.py", issue_count: 1, max_severity: "high",
              issues: [{ finding_id: "b1", severity: "high", title: "B issue", file: "b.py" }] }],
          },
        });
      }
      return Promise.resolve({ data: { items: [], total_open: 0, files: [] } });
    });

    render(<Harness />);
    await screen.findByTestId("ora-guide-mascot-avatar");

    // Switch to B BEFORE A's request resolves.
    const fixListCalls = () => apiGetMock.mock.calls.filter((c) => c[0] === "/findings/fix-list");
    act(() => { setActiveProjectForTest({ project_id: "proj-B" }); });
    await waitFor(() => expect(fixListCalls().length).toBe(2));

    // NOW resolve A's slow, stale response.
    act(() => {
      deferredA.resolve({
        data: {
          items: [{ finding_id: "a1", severity: "critical", title: "A issue", file: "a.py" }],
          total_open: 1, files: [],
        },
      });
    });
    await act(async () => { await Promise.resolve(); });

    fireEvent.click(screen.getByTestId("ora-guide-mascot-avatar"));
    // Must show B's issue, never A's stale one.
    await waitFor(() => expect(screen.queryByTestId("ora-guide-fixlist-title")).toHaveTextContent("B issue"));
  });

  it("FE2 — switching projects clears items immediately, before the new fetch resolves", async () => {
    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.project_id === "proj-A") {
        return Promise.resolve({
          data: { items: [{ finding_id: "a1", severity: "high", title: "A issue", file: "a.py" }],
                  total_open: 1, files: [] },
        });
      }
      return deferred().promise; // proj-B never resolves in this test
    });
    render(<Harness />);
    fireEvent.click(await screen.findByTestId("ora-guide-mascot-avatar"));
    await waitFor(() => expect(screen.getByTestId("ora-guide-fixlist-title")).toHaveTextContent("A issue"));
    fireEvent.click(screen.getByTestId("ora-guide-mascot-avatar")); // close

    act(() => { setActiveProjectForTest({ project_id: "proj-B" }); });
    fireEvent.click(screen.getByTestId("ora-guide-mascot-avatar"));
    // B's fetch never resolves -> must show "nothing open", NEVER A's stale item.
    await waitFor(() => expect(screen.queryByText(/A issue/)).toBeNull());
  });

  it("FE2 — a failed fetch shows a retry state, not the old project's list", async () => {
    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.project_id === "proj-A") return Promise.reject(new Error("500"));
      return Promise.resolve({ data: { items: [], total_open: 0, files: [] } });
    });
    render(<Harness />);
    await screen.findByTestId("ora-guide-mascot-avatar");
    fireEvent.click(screen.getByTestId("ora-guide-mascot-avatar"));
    await waitFor(() => expect(screen.getByTestId("ora-guide-fixlist-retry")).toBeTruthy());
  });

  it("FE3 — a fix event for a DIFFERENT project is ignored (count and list unchanged)", async () => {
    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.project_id === "proj-A") {
        return Promise.resolve({
          data: { items: [{ finding_id: "a1", severity: "high", title: "A issue", file: "a.py" }],
                  total_open: 1, files: [] },
        });
      }
      return Promise.resolve({ data: { items: [], total_open: 0, files: [] } });
    });
    render(<Harness />);
    fireEvent.click(await screen.findByTestId("ora-guide-mascot-avatar"));
    await waitFor(() => expect(screen.getByTestId("ora-guide-fixlist-title")).toHaveTextContent("A issue"));
    const callsBefore = apiGetMock.mock.calls.length;

    act(() => {
      window.dispatchEvent(new CustomEvent("aurem:finding-fixed", {
        detail: { finding_id: "a1", project_id: "proj-OTHER" },
      }));
    });
    // Still showing A's issue — the event was for a different project.
    expect(screen.getByTestId("ora-guide-fixlist-title")).toHaveTextContent("A issue");
    expect(apiGetMock.mock.calls.length).toBe(callsBefore); // no refetch triggered
  });

  it("FE3 — an unmatched finding_id never decrements the count", async () => {
    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.project_id === "proj-A") {
        return Promise.resolve({
          data: { items: [{ finding_id: "a1", severity: "high", title: "A issue", file: "a.py" }],
                  total_open: 1, files: [] },
        });
      }
      return Promise.resolve({ data: { items: [], total_open: 0, files: [] } });
    });
    render(<Harness />);
    await screen.findByTestId("ora-guide-mascot-avatar");
    await waitFor(() => expect(apiGetMock).toHaveBeenCalled());

    act(() => {
      window.dispatchEvent(new CustomEvent("aurem:finding-fixed", {
        detail: { finding_id: "not-in-the-list", project_id: "proj-A" },
      }));
    });
    fireEvent.click(screen.getByTestId("ora-guide-mascot-avatar"));
    // Badge/total must still read 1 — never blindly decremented to 0.
    await waitFor(() => expect(screen.getByTestId("ora-guide-fixlist-title")).toHaveTextContent("A issue"));
  });

  it("F3 — 'See all' groups issues by file", async () => {
    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.project_id === "proj-A") {
        const issues = [
          { finding_id: "a1", severity: "critical", title: "Hardcoded secret", file: "auth.py", line: 10 },
          { finding_id: "a2", severity: "medium", title: "Weak hash", file: "auth.py", line: 20 },
        ];
        return Promise.resolve({
          data: {
            items: issues, total_open: 2,
            files: [{ file: "auth.py", issue_count: 2, max_severity: "critical", issues }],
          },
        });
      }
      return Promise.resolve({ data: { items: [], total_open: 0, files: [] } });
    });
    render(<Harness />);
    fireEvent.click(await screen.findByTestId("ora-guide-mascot-avatar"));
    await waitFor(() => screen.getByTestId("ora-guide-fixlist-seeall"));
    fireEvent.click(screen.getByTestId("ora-guide-fixlist-seeall"));

    await waitFor(() => expect(screen.getByTestId("ora-guide-fixlist-file-auth.py")).toBeTruthy());
    expect(screen.getByText(/2 issues, worst: critical/)).toBeTruthy();
  });

  it("Part 2b R2/F2 — 'Fix this file' calls the bulk API with finding_ids, not the chat composer", async () => {
    const issues = [
      { finding_id: "a1", severity: "critical", title: "Hardcoded secret", file: "auth.py", line: 10 },
      { finding_id: "a2", severity: "medium", title: "Weak hash", file: "auth.py", line: 20 },
    ];
    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.project_id === "proj-A") {
        return Promise.resolve({
          data: {
            items: issues, total_open: 2,
            files: [{ file: "auth.py", issue_count: 2, max_severity: "critical", issues }],
          },
        });
      }
      return Promise.resolve({ data: { items: [], total_open: 0, files: [] } });
    });
    apiPostMock.mockImplementation(() => Promise.resolve({ data: { job_id: "fx_abc123" } }));

    render(<Harness />);
    fireEvent.click(await screen.findByTestId("ora-guide-mascot-avatar"));
    await waitFor(() => screen.getByTestId("ora-guide-fixlist-seeall"));
    fireEvent.click(screen.getByTestId("ora-guide-fixlist-seeall"));
    await waitFor(() => expect(screen.getByTestId("ora-guide-fixlist-file-auth.py")).toBeTruthy());

    let composerPromptFired = false;
    window.addEventListener("aurem:starter-pick", () => { composerPromptFired = true; });
    let openedJob = null;
    window.addEventListener("aurem:open-fix-progress", (e) => { openedJob = e.detail; });

    fireEvent.click(screen.getByTestId("ora-guide-fixlist-file-fix-auth.py"));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());

    expect(apiPostMock).toHaveBeenCalledWith("/fix-pipeline/bulk", {
      project_id: "proj-A", finding_ids: ["a1", "a2"],
    });
    expect(composerPromptFired).toBe(false);
    await waitFor(() => expect(openedJob).toEqual({ job_id: "fx_abc123", total: 2 }));
  });

  it("F3 — legacy flat items still render (backward compat)", async () => {
    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.project_id === "proj-A") {
        return Promise.resolve({
          data: { items: [{ finding_id: "a1", severity: "high", title: "A issue", file: "a.py" }],
                  total_open: 1 }, // no `files` key at all — legacy shape
        });
      }
      return Promise.resolve({ data: { items: [], total_open: 0 } });
    });
    render(<Harness />);
    fireEvent.click(await screen.findByTestId("ora-guide-mascot-avatar"));
    await waitFor(() => expect(screen.getByTestId("ora-guide-fixlist-title")).toHaveTextContent("A issue"));
  });
});
