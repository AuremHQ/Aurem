/**
 * Projects.simplifyFilePanel.test.jsx — Ticket F-UI (2026-02)
 * behavioral RTL tests: "Preview cleanup" on a file, showing the diff
 * + safety-gate refusal reasons, and the explicit two-step confirm
 * required before any apply (draft branch/PR) call is made.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../lib/api", () => ({ api: { post: vi.fn() } }));

import { SimplifyFilePanel } from "../Projects.jsx";
import { api } from "../../lib/api";

beforeEach(() => vi.clearAllMocks());

describe("Ticket F-UI · SimplifyFilePanel", () => {
  it("Preview button is disabled until a path is typed", () => {
    render(<SimplifyFilePanel projectId="p1" />);
    expect(screen.getByTestId("simplify-file-preview-btn")).toBeDisabled();
  });

  it("previews a clean simplify and shows a colorized diff", async () => {
    api.post.mockResolvedValue({
      data: { ok: true, refused: false, unchanged: false,
              new_content: "import os\n\ndef f():\n    return os.getcwd()\n",
              diff: "--- a/x.py\n+++ b/x.py\n-    unused = 1\n+    pass\n" },
    });
    const user = userEvent.setup();
    render(<SimplifyFilePanel projectId="p1" />);
    await user.type(screen.getByTestId("simplify-file-path-input"), "services/x.py");
    await user.click(screen.getByTestId("simplify-file-preview-btn"));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/cto/projects/p1/simplify", { path: "services/x.py" },
    ));
    await waitFor(() => expect(screen.getByTestId("simplify-file-diff")).toBeInTheDocument());
    expect(screen.getByTestId("simplify-file-diff")).toHaveTextContent("unused = 1");
    expect(screen.getByTestId("simplify-apply-btn")).toBeInTheDocument();
  });

  it("shows 'already clean' and no Apply button when there is nothing to simplify", async () => {
    api.post.mockResolvedValue({ data: { ok: true, refused: false, unchanged: true } });
    const user = userEvent.setup();
    render(<SimplifyFilePanel projectId="p1" />);
    await user.type(screen.getByTestId("simplify-file-path-input"), "clean.py");
    await user.click(screen.getByTestId("simplify-file-preview-btn"));
    await waitFor(() => expect(screen.getByTestId("simplify-file-unchanged")).toBeInTheDocument());
    expect(screen.queryByTestId("simplify-apply-btn")).toBeNull();
  });

  it("shows the safety-gate refusal reason + named violations, never a diff (real FastAPI {detail:...} envelope)", async () => {
    // FastAPI's raise HTTPException(422, result) wraps the body as
    // {"detail": result} — this mock matches the REAL wire shape
    // (confirmed live against the deployed endpoint), not a
    // convenient flattened guess.
    api.post.mockRejectedValue({
      response: { status: 422, data: { detail: {
        ok: false, refused: true,
        reason: "refusing — this edit would change behavior and/or drop existing code:",
        violations: ["dropped import: from cvss import CVSS2, CVSS3"],
      } } },
    });
    const user = userEvent.setup();
    render(<SimplifyFilePanel projectId="p1" />);
    await user.type(screen.getByTestId("simplify-file-path-input"), "services/osv_client.py");
    await user.click(screen.getByTestId("simplify-file-preview-btn"));

    await waitFor(() => expect(screen.getByTestId("simplify-file-refused")).toBeInTheDocument());
    expect(screen.getByTestId("simplify-file-refused")).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("simplify-violation-0")).toHaveTextContent("dropped import: from cvss import CVSS2, CVSS3");
    expect(screen.queryByTestId("simplify-file-diff")).toBeNull();
  });

  it("Apply requires an explicit second confirmation click before any apply call is made", async () => {
    api.post.mockResolvedValueOnce({
      data: { ok: true, refused: false, unchanged: false,
              new_content: "x = 1\n", diff: "-y=1\n+x=1\n" },
    });
    const user = userEvent.setup();
    render(<SimplifyFilePanel projectId="p1" />);
    await user.type(screen.getByTestId("simplify-file-path-input"), "a.py");
    await user.click(screen.getByTestId("simplify-file-preview-btn"));
    await waitFor(() => expect(screen.getByTestId("simplify-apply-btn")).toBeInTheDocument());

    await user.click(screen.getByTestId("simplify-apply-btn"));
    expect(api.post).toHaveBeenCalledTimes(1);
    const confirm = await screen.findByTestId("simplify-apply-confirm");
    expect(confirm).toHaveAttribute("role", "alertdialog");
    expect(confirm).toHaveTextContent(/will NOT push directly to your default branch/i);

    api.post.mockResolvedValueOnce({
      data: { ok: true, refused: false, pr_url: "https://github.com/o/r/pull/5", branch: "aurem/simplify-a-py" },
    });
    await user.click(screen.getByTestId("simplify-apply-confirm-yes"));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/cto/projects/p1/simplify/apply", { path: "a.py", new_content: "x = 1\n" },
    ));
    await waitFor(() => expect(screen.getByTestId("simplify-applied-success")).toBeInTheDocument());
    expect(screen.getByTestId("simplify-pr-link")).toHaveAttribute("href", "https://github.com/o/r/pull/5");
  });

  it("Cancel on the confirm step aborts — never calls the apply endpoint", async () => {
    api.post.mockResolvedValueOnce({
      data: { ok: true, refused: false, unchanged: false, new_content: "x=1\n", diff: "-y=1\n+x=1\n" },
    });
    const user = userEvent.setup();
    render(<SimplifyFilePanel projectId="p1" />);
    await user.type(screen.getByTestId("simplify-file-path-input"), "a.py");
    await user.click(screen.getByTestId("simplify-file-preview-btn"));
    await waitFor(() => expect(screen.getByTestId("simplify-apply-btn")).toBeInTheDocument());
    await user.click(screen.getByTestId("simplify-apply-btn"));
    await user.click(await screen.findByTestId("simplify-apply-confirm-cancel"));

    expect(screen.queryByTestId("simplify-apply-confirm")).toBeNull();
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("a 502 LLM/codegen failure (ok:false, not refused) shows the real reason, never a fake diff", async () => {
    // The backend returns HTTP 502 (not 200, not 422) when the LLM
    // call itself fails — this locks in the fix where the UI used to
    // treat ANY 200-ish/non-422 response as a successful preview.
    api.post.mockRejectedValue({
      response: { status: 502, data: { detail: {
        ok: false, refused: false,
        reason: "codegen failed: Client error '401 Unauthorized' for url 'https://openrouter.ai/api/v1/chat/completions'",
      } } },
    });
    const user = userEvent.setup();
    render(<SimplifyFilePanel projectId="p1" />);
    await user.type(screen.getByTestId("simplify-file-path-input"), "math_utils.py");
    await user.click(screen.getByTestId("simplify-file-preview-btn"));
    await waitFor(() => expect(screen.getByTestId("simplify-file-error")).toBeInTheDocument());
    expect(screen.getByTestId("simplify-file-error")).toHaveTextContent(/codegen failed/i);
    expect(screen.queryByTestId("simplify-file-diff")).toBeNull();
    expect(screen.queryByTestId("simplify-apply-btn")).toBeNull();
  });


  it("a generic preview error shows an honest message, never a silent failure", async () => {
    api.post.mockRejectedValue(new Error("network down"));
    const user = userEvent.setup();
    render(<SimplifyFilePanel projectId="p1" />);
    await user.type(screen.getByTestId("simplify-file-path-input"), "a.py");
    await user.click(screen.getByTestId("simplify-file-preview-btn"));
    await waitFor(() => expect(screen.getByTestId("simplify-file-error")).toBeInTheDocument());
    expect(screen.getByTestId("simplify-file-error")).toHaveAttribute("role", "alert");
  });

  it("the file path input has an accessible label", () => {
    render(<SimplifyFilePanel projectId="p1" />);
    expect(screen.getByLabelText("File path to simplify")).toBeInTheDocument();
  });
});
