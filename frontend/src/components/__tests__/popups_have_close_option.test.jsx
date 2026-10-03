/**
 * Every popup must give the user a visible way out: an always-visible
 * ✕ button, plus Escape. These render the real components and drive
 * them the way a user would (click ✕ / press Escape), asserting the
 * close callback actually fires — not just that markup exists.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import ModalCloseButton from "../ModalCloseButton.jsx";
import AddLiveSiteModal from "../AddLiveSiteModal.jsx";
import RepoHelpDialog from "../chat/RepoHelpDialog.jsx";
import DangerZone from "../DangerZone.jsx";
import { UpgradePopup } from "../ModeSelector.jsx";
import { RechargeModal } from "../TokenBell.jsx";
import { LastLookModal } from "../DeployPanel.jsx";
import SuggestionBoxModal from "../SuggestionBoxModal.jsx";
import Toaster, { toast } from "../Toast.jsx";

const esc = () => fireEvent.keyDown(document, { key: "Escape" });

describe("ModalCloseButton", () => {
  it("has an accessible name and fires onClick without bubbling to the backdrop", () => {
    const onClick = vi.fn();
    const backdrop = vi.fn();
    render(
      <div onClick={backdrop}>
        <ModalCloseButton onClick={onClick} testId="x" />
      </div>,
    );
    const btn = screen.getByRole("button", { name: "Close" });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(backdrop).not.toHaveBeenCalled();
  });
});

describe("popups expose a visible ✕ and close on Escape", () => {
  it("AddLiveSiteModal", () => {
    const onCancel = vi.fn();
    render(<AddLiveSiteModal projectName="p" onSave={vi.fn()} onCancel={onCancel} />);
    fireEvent.click(screen.getByTestId("add-live-site-close"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    esc();
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("RepoHelpDialog", () => {
    const onClose = vi.fn();
    render(<RepoHelpDialog project={null} onClose={onClose} onOpenProjects={vi.fn()} />);
    fireEvent.click(screen.getByTestId("repo-help-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
    esc();
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("DangerZone delete-account modal closes from ✕", () => {
    render(
      <MemoryRouter>
        <DangerZone email="a.b@example.com" />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId("danger-zone-delete-btn"));
    expect(screen.getByTestId("danger-zone-modal")).toBeTruthy();
    fireEvent.click(screen.getByTestId("danger-zone-close"));
    expect(screen.queryByTestId("danger-zone-modal")).toBeNull();
  });

  it("UpgradePopup", () => {
    const onClose = vi.fn();
    render(
      <UpgradePopup
        mode="pro"
        data={{ label: "Pro", desc: "d", min_tier: "starter", price: 10 }}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("upgrade-popup-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
    esc();
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("RechargeModal (low tokens)", () => {
    const onDismiss = vi.fn();
    render(
      <MemoryRouter>
        <RechargeModal tokens={5} onRecharge={vi.fn()} onDismiss={onDismiss} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId("recharge-modal-close"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("Deploy LastLookModal", () => {
    const onCancel = vi.fn();
    render(<LastLookModal host="h" verifyUrl="" onCancel={onCancel} onConfirm={vi.fn()} />);
    fireEvent.click(screen.getByTestId("deploy-last-look-close"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    esc();
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("SuggestionBoxModal closes on Escape", () => {
    const onClose = vi.fn();
    render(<SuggestionBoxModal open onClose={onClose} />);
    esc();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("SuggestionBoxModal ignores Escape while closed", () => {
    const onClose = vi.fn();
    render(<SuggestionBoxModal open={false} onClose={onClose} />);
    esc();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("toasts can always be dismissed", () => {
  it("a persistent toast (no auto-dismiss) disappears when ✕ is clicked", () => {
    render(<Toaster />);
    act(() => { toast({ message: "stuck", persistent: true, id: "t-persist" }); });
    expect(screen.getByText("stuck")).toBeTruthy();
    fireEvent.click(screen.getByTestId("toast-close-t-persist"));
    expect(screen.queryByText("stuck")).toBeNull();
  });

  it("✕ does not trigger the toast's own onClick action", () => {
    render(<Toaster />);
    const onClick = vi.fn();
    act(() => { toast({ message: "act", persistent: true, id: "t-act", onClick }); });
    fireEvent.click(screen.getByTestId("toast-close-t-act"));
    expect(onClick).not.toHaveBeenCalled();
    expect(screen.queryByText("act")).toBeNull();
  });
});
