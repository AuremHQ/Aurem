// ChatPanel.attach.test.jsx - Attach button wiring (source-contract test).
// ChatPanel is too heavy to mount in jsdom (SSE + Mongo fixtures), so -
// like ChatPanel.loop_chip_reset.test.jsx - this test asserts on the
// component source. Live behaviour (picker opens, chip + progress,
// upload, send-with-attachment) is verified in the preview.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const SRC = readFileSync("src/components/ChatPanel.jsx", "utf-8");

describe("ChatPanel attach button", () => {
  it("opens the picker through a named handler, never silently", () => {
    expect(SRC).toContain("chat-attach-btn");
    expect(SRC).toContain("onClick={openFilePicker}");
    expect(SRC).toContain("const openFilePicker");
    expect(SRC).toContain("fileInputRef.current");
    expect(SRC).toContain("input.click()");
    // If the input is not mounted, the user gets a toast, not silence.
    expect(SRC).toContain("Attach is unavailable right now");
    expect(SRC).not.toContain("fileInputRef.current?.click()");
  });

  it("keeps the file input rendered (visually hidden, not display:none)", () => {
    expect(SRC).toContain("chat-file-input");
    expect(SRC).toMatch(/type="file"/);
    expect(SRC).toContain("multiple");
    expect(SRC).not.toMatch(/display: "none"/);
    expect(SRC).toMatch(/position: "absolute"/);
    expect(SRC).toContain("rect(0, 0, 0, 0)");
  });

  it("offers the promised types in the picker", () => {
    const accept = SRC.match(/accept="([^"]+)"/)?.[1] ?? "";
    for (const ext of [".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".py", ".js", ".txt", ".md"]) {
      expect(accept).toContain(ext);
    }
  });

  it("hands the chosen files to handleFiles and resets the input", () => {
    expect(SRC).toContain("handleFiles(e.target.files)");
    expect(SRC).toMatch(/e[.]target[.]value = ""/);
  });

  it("uploads through /upload/convert with real progress", () => {
    expect(SRC).toContain("/upload/convert");
    expect(SRC).toContain("onUploadProgress");
    expect(SRC).toContain("progress: pct");
    expect(SRC).toContain("progress: 0");
    expect(SRC).toContain("progress: 100");
    // The transfer list must show the measured percent, not a hardcoded 0.
    expect(SRC).toContain("a.progress ?? 0");
  });

  it("enforces the 25 MB cap and shows an uploading chip first", () => {
    expect(SRC).toContain("f.size > MAX_FILE_BYTES");
    expect(SRC).toContain("exceeds 25 MB");
    expect(SRC).toMatch(/status: "uploading"/);
    expect(SRC).toContain("chat-attach-pill-");
  });

  it("sends only ready attachments with the message", () => {
    expect(SRC).toContain("attachments.filter((a) => a.status ===");
    expect(SRC).toContain("attachmentBlock");
  });
});
