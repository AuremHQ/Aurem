// ChatPanel.turnsScope.test.jsx - regression test for the Dashboard
// crash "ReferenceError: turns is not defined" (Fri 2026-11-20).
// The render referenced turns, which only exists as a local inside
// the history loader; the component state is messages. The crash
// took down the whole Dashboard (RouteErrorBoundary) on every load.
// Source-contract test, like ChatPanel.attach.test.jsx.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// Phase 1 mobile view (2026-11-24): the composer (textarea + Send/Stop +
// attach button) was extracted from ChatPanel.jsx into
// components/chat/ChatComposer.jsx, so the contract now spans both files.
// The assertions below are unchanged -- they still guard the same behaviour.
const SRC =
  readFileSync("src/components/ChatPanel.jsx", "utf-8") +
  readFileSync("src/components/chat/ChatComposer.jsx", "utf-8");

describe("ChatPanel render scope (turns crash regression)", () => {
  it("never references an out-of-scope turns variable in the render", () => {
    expect(SRC).not.toContain("{turns.length");
  });

  it("drives the empty and loading states from the messages state", () => {
    expect(SRC).toContain("{messages.length === 1");
    expect(SRC).toContain("{messages.length === 0");
    expect(SRC).toContain("const [messages, setMessages] = useState([WELCOME]);");
  });

  it("keeps the loader-local turns for history hydration only", () => {
    expect(SRC).toContain("const turns = (r.data?.messages || []).filter(");
    expect(SRC).toContain("if (turns.length === 0) {");
  });
});
