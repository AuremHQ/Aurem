/**
 * useModalA11y.focusStability.test.jsx — 2026-10-24
 *
 * Founder report: typing a branch name in the "Save to GitHub" modal
 * kept losing focus after about one word, forcing a re-click to keep
 * typing. Root cause: this hook's "move focus into the modal" effect
 * depended on `onClose` (and `initialFocus`) — and MessageBubble.jsx
 * passed an inline `() => setShowSaveModal(false)` as `onCancel`,
 * getting a brand-new function identity on every re-render (e.g. the
 * ChatPanel ship-phase countdown poll firing every ~5s while the
 * modal was open). Each such re-render re-ran the effect, which
 * force-focused the modal's FIRST focusable element — stealing focus
 * away from whatever input the user was actively typing in.
 *
 * This locks in the fix: the focus-into-modal effect must only run on
 * actual open/close, never merely because a caller passed a fresh
 * inline callback.
 */
import React, { useRef, useState } from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import useModalA11y from "../useModalA11y.js";

function Harness({ unstableOnClose = true }) {
  const [, forceRerender] = useState(0);
  const modalRef = useRef(null);
  // The exact anti-pattern MessageBubble.jsx had: a fresh arrow
  // function every render when `unstableOnClose` is true.
  const onClose = unstableOnClose ? () => {} : stableNoop;
  useModalA11y({ ref: modalRef, isOpen: true, onClose });
  return (
    <div ref={modalRef} role="dialog">
      <button data-testid="first-el">First (e.g. Cancel)</button>
      <input data-testid="branch-input" />
      <button
        data-testid="force-rerender"
        onClick={() => forceRerender((n) => n + 1)}
      >
        Simulate parent re-render (e.g. 5s poll)
      </button>
    </div>
  );
}
function stableNoop() {}

describe("useModalA11y — focus stability across parent re-renders", () => {
  it("does NOT steal focus back to the first element when onClose is a fresh reference each render", () => {
    render(<Harness unstableOnClose />);
    const input = screen.getByTestId("branch-input");
    input.focus();
    fireEvent.change(input, { target: { value: "fix" } });
    expect(document.activeElement).toBe(input);

    // Simulate the background poll re-rendering the tree with a BRAND
    // NEW inline onClose, same as the real bug.
    act(() => { screen.getByTestId("force-rerender").click(); });

    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "fix-more-words" } });
    expect(input.value).toBe("fix-more-words");
  });

  it("still focuses the first element on genuine open (sanity check the a11y behavior wasn't gutted)", () => {
    function OpenToggle() {
      const [open, setOpen] = useState(false);
      const modalRef = useRef(null);
      useModalA11y({ ref: modalRef, isOpen: open, onClose: () => setOpen(false) });
      return (
        <div>
          <button data-testid="opener" onClick={() => setOpen(true)}>Open</button>
          {open && (
            <div ref={modalRef} role="dialog">
              <button data-testid="first-el">First</button>
            </div>
          )}
        </div>
      );
    }
    render(<OpenToggle />);
    act(() => { screen.getByTestId("opener").click(); });
    expect(document.activeElement).toBe(screen.getByTestId("first-el"));
  });
});
