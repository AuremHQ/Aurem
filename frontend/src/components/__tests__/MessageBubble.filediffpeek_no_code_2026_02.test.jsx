/**
 * MessageBubble.filediffpeek_no_code_2026_02.test.jsx
 *
 * Founder-reported CRITICAL regression, reliably reproducible 3/3
 * times with "add a comment '<any text>' to package.json or a valid
 * file": every well-formed `aurem-handoff` reply naming a REAL
 * existing file (correct per that fence's own contract — no code
 * block inside, the worker writes the code from the brief) rendered
 * a `FileDiffPeek` chip showing a false, deterministic "everything
 * in the file deleted" diff on hover — e.g. "+2 / -47" with React,
 * ReactDOM, ./index.css, ./App imports all shown as removed.
 *
 * Root cause (traced end-to-end, NOT the same mechanism as the
 * earlier stray-code-block fix in commit_boundary.py — this is a
 * pure frontend bug, independent of any backend guard):
 * `extractShipFiles()` scanned the BRIEF's plain-English prose for
 * file-path-looking tokens (`FILE_RX`) and returned an entry for
 * EVERY match, with `code: block || ""` — `block` is only non-empty
 * when a REAL fenced code snippet in the message happens to mention
 * that filename. A correctly-formatted handoff (no code block at
 * all, by design) always has `block === undefined`, so `code`
 * became `""`. `FileDiffPeek` then fetched the REAL current file
 * from GitHub and ran `naiveDiff(realContent, "")` — every real line
 * has no counterpart in an empty string, so every line renders as
 * deleted. This fired on literally every well-formed plan-only
 * handoff mentioning an existing file, 100% deterministically —
 * matching the founder's exact "3/3 reliable repro" report.
 *
 * Fix: `extractShipFiles()` now SKIPS any path with no real matched
 * code block — nothing to preview means no diff chip at all (there
 * is no proposed change yet, only a plan). `FileDiffPeek` also
 * guards defensively against an empty `proposedCode` prop.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import MessageBubble from "../MessageBubble.jsx";

const mockGet = vi.fn(() => Promise.resolve({ data: { content: "line1\nline2\n" } }));
vi.mock("../../lib/api", () => ({
  api: { get: (...args) => mockGet(...args), post: vi.fn(() => Promise.resolve({ data: {} })) },
  getToken: () => null,
}));
vi.mock("../Toast", () => ({ toast: vi.fn() }));

const ACTIVE_PROJECT = {
  project_id: "proj_1", github_owner: "acme", github_repo: "widgets", branch: "main",
};

describe("FileDiffPeek — no false destructive diff for a plan-only handoff (founder repro)", () => {
  it("t_plan_only_handoff_renders_no_peek_row_at_all: 'add a comment to index.js' shape, no code block", () => {
    const planOnly = [
      "I can see your repo has index.js. There's no package.json here "
        + "with a valid comment slot, so I'll add it to the React entry file.",
      "",
      "```aurem-handoff",
      "In `frontend/src/index.js` add the comment `// qa regression test` "
        + "at the top of the file, above the existing imports.",
      "```",
    ].join("\n");
    render(
      <MemoryRouter>
        <MessageBubble
          idx={0}
          m={{ role: "assistant", content: planOnly, provider: "mock", streaming: false }}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    // The Approve button still renders (a real, valid handoff) —
    // this fix must not break the legitimate ship flow.
    expect(screen.getByTestId("ship-cto-0-approve-btn")).toBeInTheDocument();
    // But NO file-diff-peek row — there's no proposed code to
    // preview, so no false "everything deleted" chip.
    expect(screen.queryByTestId("file-peek-row-0")).toBeNull();
    expect(
      screen.queryByTestId("file-diff-peek-frontend-src-index-js"),
    ).toBeNull();
  });

  it("t_real_code_block_still_shows_a_real_peek_no_regression: a handoff WITH an actual proposed snippet still works", () => {
    const withCode = [
      "Here's the fix:",
      "```js",
      "// frontend/src/utils/math.js",
      "export function add(a, b) { return a + b; }",
      "```",
      "",
      "```aurem-handoff",
      "Apply the fix to frontend/src/utils/math.js as shown above.",
      "```",
    ].join("\n");
    render(
      <MemoryRouter>
        <MessageBubble
          idx={1}
          m={{ role: "assistant", content: withCode, provider: "mock", streaming: false }}
          sessionId="sess-1"
          activeProject={ACTIVE_PROJECT}
          exhausted={false}
        />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("file-peek-row-1")).toBeInTheDocument();
    expect(
      screen.getByTestId("file-diff-peek-frontend-src-utils-math-js"),
    ).toBeInTheDocument();
  });

  it("t_file_diff_peek_defensive_guard_never_fetches_on_empty_proposed_code", async () => {
    const FileDiffPeek = (await import("../FileDiffPeek.jsx")).default;
    mockGet.mockClear();
    render(<FileDiffPeek path="frontend/src/index.js" projectId="proj_1" proposedCode="" />);
    const chip = screen.getByTestId("file-diff-peek-frontend-src-index-js");
    fireEvent.mouseEnter(chip);
    await new Promise((r) => setTimeout(r, 400));
    expect(mockGet).not.toHaveBeenCalled();
    expect(
      screen.queryByTestId("file-diff-tooltip-frontend-src-index-js"),
    ).toBeNull();
  });

  it("t_file_diff_peek_still_fetches_and_shows_real_diff_when_code_is_present", async () => {
    const FileDiffPeek = (await import("../FileDiffPeek.jsx")).default;
    mockGet.mockClear();
    render(
      <FileDiffPeek
        path="frontend/src/utils/math.js"
        projectId="proj_1"
        proposedCode="export function add(a, b) { return a + b; }"
      />,
    );
    const chip = screen.getByTestId("file-diff-peek-frontend-src-utils-math-js");
    fireEvent.mouseEnter(chip);
    await waitFor(() => expect(mockGet).toHaveBeenCalled(), { timeout: 1000 });
  });
});
