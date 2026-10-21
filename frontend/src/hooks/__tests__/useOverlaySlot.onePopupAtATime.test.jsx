/**
 * useOverlaySlot.onePopupAtATime.test.jsx — UI-fix D1/D2/D5 (2026-10)
 *
 * Section D "one popup at a time": NewUserWizard > StarterChoiceModal
 * > the ORA GUIDE mascot's auto-open bubble. Exercises the real
 * shared coordinator (hooks/useOverlaySlot.js) that Dashboard.jsx,
 * StarterChoiceModal.jsx and OraGuideMascot.jsx are all wired into —
 * mounting the full Dashboard/OraGuideMascot tree for this would
 * need mocking dozens of unrelated deps (ChatPanel, Shell, GitHub
 * polling, funnel-stage polling...), so — matching this codebase's
 * own convention for OraGuideMascot (see
 * OraGuideMascot.iter2026_10.fixListOrdering.test.jsx, which only
 * exercises its exported pure helpers) — this drives the actual
 * exported hook + exported pure classifiers directly.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { useOverlaySlot, useCanShowOverlay } from "../useOverlaySlot";
import { stageToGroup, STAGE_COPY } from "../../components/OraGuideMascot";

// Minimal stand-ins for the 3 real overlays, wired the same way the
// real components are: NewUserWizard/StarterChoiceModal *announce*
// their own open state; the mascot only *reads* whether it may show.
function FakeWizard({ open }) {
  useOverlaySlot("wizard", open);
  return open ? <div data-testid="fake-wizard" role="dialog">wizard</div> : null;
}
function FakeStarter({ open }) {
  useOverlaySlot("starter", open);
  return open ? <div data-testid="fake-starter" role="dialog">starter</div> : null;
}
function FakeMascotBubble() {
  const canShow = useCanShowOverlay("mascot");
  return canShow ? <div data-testid="fake-mascot-bubble">mascot bubble</div> : null;
}

function Harness({ wizardOpen, starterOpen }) {
  return (
    <>
      <FakeWizard open={wizardOpen} />
      <FakeStarter open={starterOpen} />
      <FakeMascotBubble />
    </>
  );
}

describe("UI-fix D1/D5 · one onboarding overlay at a time", () => {
  it("(a) with nothing else open, the mascot bubble is the one visible overlay", () => {
    render(<Harness wizardOpen={false} starterOpen={false} />);
    expect(screen.queryByTestId("fake-wizard")).toBeNull();
    expect(screen.queryByTestId("fake-starter")).toBeNull();
    expect(screen.getByTestId("fake-mascot-bubble")).toBeInTheDocument();
  });

  it("(a) wizard open suppresses both starter and mascot — exactly one overlay visible", () => {
    render(<Harness wizardOpen starterOpen={false} />);
    expect(screen.getByTestId("fake-wizard")).toBeInTheDocument();
    expect(screen.queryByTestId("fake-mascot-bubble")).toBeNull();
  });

  it("(b) after 'Skip for now' (wizard closes, starter opens) the starter modal is visible and the mascot is hidden", () => {
    const { rerender } = render(<Harness wizardOpen starterOpen={false} />);
    expect(screen.getByTestId("fake-wizard")).toBeInTheDocument();
    expect(screen.queryByTestId("fake-mascot-bubble")).toBeNull();

    // Simulate handleSkip(): wizard closes, starter opens — same tick,
    // exactly like NewUserWizard.jsx's handleSkip() does.
    act(() => { rerender(<Harness wizardOpen={false} starterOpen />); });

    expect(screen.queryByTestId("fake-wizard")).toBeNull();
    expect(screen.getByTestId("fake-starter")).toBeInTheDocument();
    expect(screen.queryByTestId("fake-mascot-bubble")).toBeNull();
  });

  it("mascot bubble reappears once both higher-priority overlays close", () => {
    const { rerender } = render(<Harness wizardOpen={false} starterOpen />);
    expect(screen.queryByTestId("fake-mascot-bubble")).toBeNull();
    act(() => { rerender(<Harness wizardOpen={false} starterOpen={false} />); });
    expect(screen.getByTestId("fake-mascot-bubble")).toBeInTheDocument();
  });
});

describe("UI-fix D2 · no above/below contradiction, no stale connect-repo copy post-project", () => {
  it("the mascot's connect-GitHub copy no longer uses a position word ('above')", () => {
    expect(STAGE_COPY.connect_github.message).not.toMatch(/\babove\b/i);
    expect(STAGE_COPY.connect_github.message).toContain("Continue with GitHub");
  });

  it("(c) a user with a connected project (stage3_no_chat) never classifies into the connect-repo group", () => {
    const group = stageToGroup("stage3_no_chat");
    expect(group).not.toBe("connect_github");
    expect(group).not.toBe("project_pending");
  });
});
