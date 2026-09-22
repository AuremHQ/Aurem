/**
 * 2026-09 — founder bug report: the chat composer textarea was
 * silently letting Safari's smart-punctuation substitution corrupt
 * literal input ("--" -> en-dash, straight quotes -> curly quotes)
 * before it ever reached the backend, breaking pasted CLI flags,
 * code, and SQL.
 *
 * Locks in the contract for `preventSmartPunctuation` in
 * `utils/chatTextUtils.js`: it must call `preventDefault()` ONLY on
 * the native `beforeinput` event WebKit fires when it's about to
 * swap in a smart-punctuation replacement (`inputType ===
 * "insertReplacementText"`), and must be a no-op for every other
 * input type so normal typing, IME composition, paste, etc. are
 * completely unaffected.
 */
import { describe, it, expect, vi } from "vitest";
import { preventSmartPunctuation } from "../chatTextUtils";

describe("2026-09 — preventSmartPunctuation (Safari smart-punctuation guard)", () => {
  it("calls preventDefault() when inputType is 'insertReplacementText' (the smart-quote/dash swap)", () => {
    const e = { inputType: "insertReplacementText", preventDefault: vi.fn() };

    preventSmartPunctuation(e);

    expect(e.preventDefault).toHaveBeenCalledTimes(1);
  });

  it("does NOT call preventDefault() for normal typed characters", () => {
    const e = { inputType: "insertText", preventDefault: vi.fn() };

    preventSmartPunctuation(e);

    expect(e.preventDefault).not.toHaveBeenCalled();
  });

  it("does NOT call preventDefault() for paste, deletion, or IME composition", () => {
    for (const inputType of [
      "insertFromPaste",
      "deleteContentBackward",
      "insertCompositionText",
      undefined,
    ]) {
      const e = { inputType, preventDefault: vi.fn() };
      preventSmartPunctuation(e);
      expect(e.preventDefault).not.toHaveBeenCalled();
    }
  });

  // 2026-09 bug brief — exact reproduction string: "npm run build --
  // --force and git commit -m 'x'". Every position where the browser
  // MIGHT attempt a smart-punctuation swap (the "--" runs, the two
  // straight quotes) must be blocked; every normal character must
  // pass through completely untouched.
  it("test_chat_input_preserves_literal_dashes_and_quotes — brief repro string", () => {
    const literal = "npm run build -- --force and git commit -m 'x'";

    // Simulate the browser attempting its smart-punctuation swap at
    // every character position — the guard must block ALL of them,
    // regardless of position, so the literal char always wins.
    for (let i = 0; i < literal.length; i++) {
      const e = { inputType: "insertReplacementText", preventDefault: vi.fn() };
      preventSmartPunctuation(e);
      expect(e.preventDefault).toHaveBeenCalledTimes(1);
    }

    // And confirm ordinary typing of this exact string is never
    // touched — no false positives on normal characters.
    for (let i = 0; i < literal.length; i++) {
      const e = { inputType: "insertText", data: literal[i], preventDefault: vi.fn() };
      preventSmartPunctuation(e);
      expect(e.preventDefault).not.toHaveBeenCalled();
    }
  });
});
