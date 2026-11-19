import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { AppStateBanners, BackgroundProcessing, CancelledState, CompletionState, ConfirmationDialog, ConflictState, DisabledButton, EmptyState, ErrorState, FieldError, FileTransferList, FirstTimeChecklist, formatBytes, formatCountdown, LoadingSkeleton, MaintenanceState, MultiStepForm, NoResultsState, OfflineBanner, OptimisticToggle, PartialSuccess, PermissionDenied, ProgressState, QueueState, RateLimitState, ReconnectingBanner, SavingButton, SessionExpiredBanner, StaleDataNotice, SuccessState, SyncIndicator, TimeoutState, UndoToast, UnsavedChangesDialog, UpdateRequired, WarningState } from "../ui-states/UiStates.jsx";

describe("Ora UI states", () => {
  it("EmptyState shows a reason and one next-step button", () => {
    const onAction = vi.fn();
    render(<EmptyState title="No projects yet" message="You have not created a project yet." actionLabel="Create a project" onAction={onAction} />);
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByText("You have not created a project yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Create a project"));
    expect(onAction).toHaveBeenCalled();
  });

  it("LoadingSkeleton uses labelled placeholders, not a blank screen", () => {
    render(<LoadingSkeleton label="Loading projects…" />);
    expect(screen.getByRole("status", { name: "Loading projects…" })).toBeInTheDocument();
  });

  it("SavingButton disables while saving and says Saving changes", () => {
    render(<SavingButton saving onClick={() => {}}>Save</SavingButton>);
    const button = screen.getByRole("button");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Saving changes");
  });

  it("ProgressState shows exact percent, bytes and file size", () => {
    render(<ProgressState fileName="report.pdf" fileSize={2048} bytesDone={1024} />);
    expect(screen.getByText(/50% complete/)).toBeInTheDocument();
    expect(screen.getByText(/1.0 KB of 2.0 KB/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  });

  it("ErrorState explains in plain words with retry and support", () => {
    const onRetry = vi.fn();
    render(<ErrorState message="We could not load your projects." onRetry={onRetry} />);
    expect(screen.getByText("We could not load your projects.")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Try again"));
    expect(onRetry).toHaveBeenCalled();
    expect(screen.getByText("Get help from support")).toHaveAttribute("href", "/support");
  });

  it("PermissionDenied names the exact role required", () => {
    render(<PermissionDenied roleRequired="Admin" contactName="Teji" onRequestAccess={() => {}} onGoBack={() => {}} />);
    expect(screen.getByText(/Admin/)).toBeInTheDocument();
    expect(screen.getByText("Request access")).toBeInTheDocument();
    expect(screen.getByText("Go back")).toBeInTheDocument();
  });

  it("SuccessState shows reference, amount and recipient", () => {
    render(<SuccessState title="Payment done" reference="ORA-123" amount="$19" recipient="Ora" nextLabel="View receipt" onNext={() => {}} />);
    expect(screen.getByText("ORA-123")).toBeInTheDocument();
    expect(screen.getByText("$19")).toBeInTheDocument();
    expect(screen.getByText("Ora")).toBeInTheDocument();
  });

  it("NoResultsState shows the query and offers clear filters", () => {
    const onClear = vi.fn();
    render(<NoResultsState query="zebra" filters={["Active"]} suggestions={["Check the spelling"]} onClearFilters={onClear} />);
    expect(screen.getByText(/zebra/)).toBeInTheDocument();
    expect(screen.getByText(/Active/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Clear filters"));
    expect(onClear).toHaveBeenCalled();
  });

  it("FieldError names the exact fix below the field", () => {
    render(<FieldError id="email-error" message="Enter a valid email address, like name@example.ca." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid email address");
  });

  it("WarningState names consequences and uses a destructive action", () => {
    const onConfirm = vi.fn();
    render(<WarningState title="Delete project" consequences="This will delete 42 files. This cannot be undone." confirmLabel="Delete project" onConfirm={onConfirm} onCancel={() => {}} />);
    expect(screen.getByText(/42 files/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Delete project", { selector: "button" }));
    expect(onConfirm).toHaveBeenCalled();
  });
  it("OfflineBanner says drafts are kept on this device", () => {
    render(<OfflineBanner />);
    expect(screen.getByText(/You are offline/)).toBeInTheDocument();
    expect(screen.getByText(/drafts are kept on this device/)).toBeInTheDocument();
  });

  it("ReconnectingBanner shows the attempt counter", () => {
    render(<ReconnectingBanner attempt={2} total={5} onRetry={() => {}} />);
    expect(screen.getByText(/Attempt 2 of 5/)).toBeInTheDocument();
    expect(screen.getByText("Retry now")).toBeInTheDocument();
  });

  it("SessionExpiredBanner keeps the email visible", () => {
    render(<SessionExpiredBanner email="teji@example.ca" onSignIn={() => {}} />);
    expect(screen.getByText(/teji@example.ca/)).toBeInTheDocument();
    expect(screen.getByText(/return to the exact page/)).toBeInTheDocument();
  });

  it("UnsavedChangesDialog offers three choices and names the item", () => {
    render(<UnsavedChangesDialog itemName="Project settings" onSave={() => {}} onDiscard={() => {}} onStay={() => {}} />);
    expect(screen.getByText(/Project settings/)).toBeInTheDocument();
    expect(screen.getByText("Save and continue")).toBeInTheDocument();
    expect(screen.getByText("Discard changes")).toBeInTheDocument();
    expect(screen.getByText("Stay on this page")).toBeInTheDocument();
  });

  it("PartialSuccess gives an exact tally and retry-only-failed", () => {
    const onRetry = vi.fn();
    render(<PartialSuccess done={2} total={3} failures={[{ name: "file-b.csv", reason: "Network error" }]} onRetryFailed={onRetry} />);
    expect(screen.getByText("2 of 3 done")).toBeInTheDocument();
    expect(screen.getByText(/Network error/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry only the failed items"));
    expect(onRetry).toHaveBeenCalled();
  });

  it("DisabledButton is faded and explains what is missing", () => {
    render(<DisabledButton disabled hint="Add a project name first." onClick={() => {}}>Create</DisabledButton>);
    const button = screen.getByRole("button", { name: "Create" });
    expect(button).toBeDisabled();
    expect(button).toHaveStyle({ opacity: "0.4" });
    expect(screen.getByText("Add a project name first.")).toBeInTheDocument();
  });

  it("ConfirmationDialog names the subject and the action verb", () => {
    const onConfirm = vi.fn();
    render(<ConfirmationDialog subject="Maya Chen" consequence="Maya will lose access to this project right away." confirmLabel="Remove member" onConfirm={onConfirm} onCancel={() => {}} />);
    expect(screen.getByText(/Remove member: Maya Chen/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove member" }));
    expect(onConfirm).toHaveBeenCalled();
  });

  it("TimeoutState warns against double payment and shows the reference", () => {
    render(<TimeoutState referenceId="ORA-999" onCheckStatus={() => {}} />);
    expect(screen.getByText(/do not try to pay again yet/)).toBeInTheDocument();
    expect(screen.getByText(/ORA-999/)).toBeInTheDocument();
    expect(screen.getByText("Check status")).toBeInTheDocument();
  });

  it("ConflictState shows who edited and offers merge options", () => {
    render(<ConflictState editorName="Maya" editedAt="2:10 PM" mine="My text" theirs="Maya text" onMerge={() => {}} onKeepMine={() => {}} onSaveCopy={() => {}} />);
    expect(screen.getByText("Maya version")).toBeInTheDocument();
    expect(screen.getByText("Merge both versions")).toBeInTheDocument();
    expect(screen.getByText("Keep my version")).toBeInTheDocument();
    expect(screen.getByText("Save as a copy")).toBeInTheDocument();
  });

  it("FileTransferList shows per-file progress and per-file retry", () => {
    const onRetry = vi.fn();
    render(<FileTransferList files={[{ id: "a", name: "a.png", size: 1024, percent: 100, status: "failed" }, { id: "b", name: "b.png", size: 2048, percent: 40, status: "uploading" }]} onRetry={onRetry} onCancel={() => {}} />);
    expect(screen.getByText(/a.png/)).toBeInTheDocument();
    expect(screen.getByText(/40%/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry"));
    expect(onRetry).toHaveBeenCalledWith("a");
  });
  it("BackgroundProcessing reassures and gives an estimate", () => {
    render(<BackgroundProcessing title="Building your app" estimatedMinutes={5} onViewStatus={() => {}} />);
    expect(screen.getByText(/keep running in the background/)).toBeInTheDocument();
    expect(screen.getByText(/about 5 minutes left/)).toBeInTheDocument();
    expect(screen.getByText(/notify you by email/)).toBeInTheDocument();
  });

  it("CancelledState assures zero side effects and offers restart", () => {
    render(<CancelledState message="Upload cancelled. Nothing was changed." onRestart={() => {}} />);
    expect(screen.getByText(/Nothing was changed/)).toBeInTheDocument();
    expect(screen.getByText("Start again")).toBeInTheDocument();
  });

  it("MaintenanceState gives UTC and local return times and a status link", () => {
    render(<MaintenanceState returnTimeUtc="22:00" returnTimeLocal="6:00 PM EST" statusHref="/support" />);
    expect(screen.getByText(/22:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/6:00 PM EST/)).toBeInTheDocument();
    expect(screen.getByText("Check the status page")).toHaveAttribute("href", "/support");
  });

  it("FirstTimeChecklist greets by name with three steps and Skip", () => {
    render(<FirstTimeChecklist name="Teji" steps={["Create a project", "Invite your team", "Ship your first app"]} onStart={() => {}} onSkip={() => {}} />);
    expect(screen.getByText("Welcome, Teji")).toBeInTheDocument();
    expect(screen.getByText("Ship your first app")).toBeInTheDocument();
    expect(screen.getByText("Skip for now")).toBeInTheDocument();
  });

  it("CompletionState celebrates and links to the dashboard", () => {
    render(<CompletionState title="Your app is live" summary="3 steps done, 0 errors." onGoToDashboard={() => {}} />);
    expect(screen.getByText("Your app is live")).toBeInTheDocument();
    expect(screen.getByText("Go to dashboard")).toBeInTheDocument();
  });

  it("OptimisticToggle flips instantly without waiting", () => {
    const onChange = vi.fn();
    render(<OptimisticToggle checked={false} onChange={onChange} label="Notifications" />);
    fireEvent.click(screen.getByRole("switch", { name: "Notifications" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("UndoToast names the item and offers Undo", () => {
    const onUndo = vi.fn();
    render(<UndoToast itemName="Project Alpha" onUndo={onUndo} duration={8000} />);
    expect(screen.getByText(/Project Alpha removed/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Undo"));
    expect(onUndo).toHaveBeenCalled();
  });

  it("RateLimitState shows a live countdown", () => {
    render(<RateLimitState retryInSeconds={45} />);
    expect(screen.getByText(/Retry in 45s/)).toBeInTheDocument();
    expect(screen.getByText(/0:45/)).toBeInTheDocument();
  });

  it("QueueState shows real position and wait estimate", () => {
    render(<QueueState position={14} total={80} estimatedMinutes={6} />);
    expect(screen.getByText(/#14 of 80/)).toBeInTheDocument();
    expect(screen.getByText(/about 6 minutes to wait/)).toBeInTheDocument();
  });

  it("StaleDataNotice shows the cache timestamp and refresh", () => {
    render(<StaleDataNotice lastUpdatedMinutes={25} onRefresh={() => {}} critical />);
    expect(screen.getByText(/Last updated 25 mins ago/)).toBeInTheDocument();
    expect(screen.getByText("Refresh")).toBeInTheDocument();
  });

  it("SyncIndicator distinguishes cloud from local and retries on error", () => {
    const onRetry = vi.fn();
    const { rerender } = render(<SyncIndicator status="synced" lastSynced="2:00 PM" />);
    expect(screen.getByText(/Saved to the cloud/)).toBeInTheDocument();
    rerender(<SyncIndicator status="error" onRetry={onRetry} />);
    expect(screen.getByText(/Saved locally only/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry sync"));
    expect(onRetry).toHaveBeenCalled();
  });

  it("MultiStepForm shows numbered progress and a safe Back", () => {
    render(<MultiStepForm step={2} total={4} steps={["Details", "Plan", "Review", "Done"]} onBack={() => {}}><p>Step content</p></MultiStepForm>);
    expect(screen.getByText("Step 2 of 4")).toBeInTheDocument();
    expect(screen.getByText("Back")).toBeInTheDocument();
    expect(screen.getByText(/saved automatically/)).toBeInTheDocument();
  });

  it("UpdateRequired distinguishes a mandatory gate from an optional banner", () => {
    const { rerender } = render(<UpdateRequired mandatory version="2.1.0" highlights={["Faster builds"]} storeHref="/pricing" />);
    expect(screen.getByText(/Update required to continue/)).toBeInTheDocument();
    expect(screen.getByText(/2.1.0/)).toBeInTheDocument();
    rerender(<UpdateRequired mandatory={false} version="2.1.0" storeHref="/pricing" onDismiss={() => {}} />);
    expect(screen.getByText(/new version is available/)).toBeInTheDocument();
    expect(screen.getByText("Not now")).toBeInTheDocument();
  });

  it("formatBytes and formatCountdown are exact", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatCountdown(45)).toBe("0:45");
    expect(formatCountdown(90)).toBe("1:30");
  });

  it("AppStateBanners shows Session Expired when the event fires", () => {
    render(<AppStateBanners sessionEmail="teji@example.ca" onSignIn={() => {}} />);
    act(() => { window.dispatchEvent(new CustomEvent("ora:session-expired", { detail: { email: "teji@example.ca" } })); });
    expect(screen.getByText("Your session has expired")).toBeInTheDocument();
    expect(screen.getByText(/teji@example.ca/)).toBeInTheDocument();
  });
});
