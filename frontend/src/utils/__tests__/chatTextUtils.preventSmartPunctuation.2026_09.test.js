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
});
