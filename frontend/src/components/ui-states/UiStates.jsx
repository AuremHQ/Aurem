import React, { useEffect, useState } from "react";
import "./UiStates.css";

export function formatBytes(bytes) {
  if (bytes == null || Number.isNaN(bytes)) return "";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

export function formatCountdown(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

export function EmptyState({ icon = "┭", title, message, actionLabel, onAction, secondaryLabel, onSecondary }) {
  return (<section className="ora-ui-card" data-testid="ora-empty-state" role="status" aria-label={title}>
    <div className="ora-ui-icon" aria-hidden="true">{icon}</div>
    <h2 className="ora-ui-title">{title}</h2>
    <p className="ora-ui-text">{message}</p>
    <div className="ora-ui-row">
      {actionLabel && <button type="button" className="ora-ui-btn" onClick={onAction}>{actionLabel}</button>}
      {secondaryLabel && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onSecondary}>{secondaryLabel}</button>}
    </div>
  </section>);
}

export function LoadingSkeleton({ lines = 3, label = "Loading content…" }) {
  return (<div data-testid="ora-loading-skeleton" role="status" aria-label={label} aria-live="polite">
    <span className="ora-ui-text">{label}</span>
    {Array.from({ length: lines }, (_, i) => (<div key={i} className="ora-ui-skeleton" style={{ height: 18, marginTop: 10, width: i === lines - 1 ? "60%" : "100%" }} aria-hidden="true" />))}
  </div>);
}

export function SavingButton({ saving, disabled, onClick, children, savingLabel = "Saving changes…" }) {
  return (<button type="button" className="ora-ui-btn" disabled={saving || disabled} onClick={onClick} aria-busy={saving} aria-live="polite">
    {saving ? savingLabel : children}
  </button>);
}

export function ProgressState({ fileName, fileSize, bytesDone = 0, percent, onCancel }) {
  const pct = percent != null ? percent : (fileSize ? Math.round((bytesDone / fileSize) * 100) : 0);
  return (<section className="ora-ui-card" data-testid="ora-progress-state" role="status" aria-label={"Uploading " + fileName + ", " + pct + " percent"}>
    <h2 className="ora-ui-title">Uploading {fileName}</h2>
    <p className="ora-ui-text">{pct}% complete — {formatBytes(bytesDone)} of {formatBytes(fileSize)}</p>
    <div className="ora-ui-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><div style={{ width: pct + "%" }} /></div>
    {onCancel && <div className="ora-ui-row" style={{ marginTop: 12 }}><button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onCancel}>Cancel upload</button></div>}
  </section>);
}
export function ErrorState({ title = "Something went wrong", message, onRetry, supportHref = "/support" }) {
  return (<section className="ora-ui-card" data-testid="ora-error-state" role="alert">
    <div className="ora-ui-icon" aria-hidden="true">⚠️</div>
    <h2 className="ora-ui-title">{title}</h2>
    <p className="ora-ui-text">{message}</p>
    <div className="ora-ui-row">
      {onRetry && <button type="button" className="ora-ui-btn" onClick={onRetry}>Try again</button>}
      <a className="ora-ui-link" href={supportHref}>Get help from support</a>
    </div>
  </section>);
}

export function PermissionDenied({ roleRequired, contactName, contactHref = "/support", onRequestAccess, onGoBack }) {
  return (<section className="ora-ui-card" data-testid="ora-permission-denied" role="alert">
    <div className="ora-ui-icon" aria-hidden="true">🔒</div>
    <h2 className="ora-ui-title">You do not have access to this page</h2>
    <p className="ora-ui-text">This page needs the <strong>{roleRequired}</strong> role.{contactName ? " Please contact " + contactName + " to ask for access." : ""}</p>
    <div className="ora-ui-row">
      {onRequestAccess && <button type="button" className="ora-ui-btn" onClick={onRequestAccess}>Request access</button>}
      {onGoBack && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onGoBack}>Go back</button>}
      <a className="ora-ui-link" href={contactHref}>Contact support</a>
    </div>
  </section>);
}

export function SuccessState({ title = "Done", reference, amount, recipient, nextLabel, onNext }) {
  return (<section className="ora-ui-card" data-testid="ora-success-state" role="status">
    <span className="ora-ui-badge ora-ui-badge-success">✓ Success</span>
    <h2 className="ora-ui-title" style={{ marginTop: 10 }}>{title}</h2>
    <dl className="ora-ui-summary">
      {reference && <><dt>Reference number</dt><dd>{reference}</dd></>}
      {amount && <><dt>Amount</dt><dd>{amount}</dd></>}
      {recipient && <><dt>Recipient</dt><dd>{recipient}</dd></>}
    </dl>
    {nextLabel && <button type="button" className="ora-ui-btn" onClick={onNext}>{nextLabel}</button>}
  </section>);
}

export function NoResultsState({ query, filters = [], suggestions = [], onClearFilters }) {
  return (<section className="ora-ui-card" data-testid="ora-no-results-state" role="status">
    <div className="ora-ui-icon" aria-hidden="true">🔍</div>
    <h2 className="ora-ui-title">No results found</h2>
    <p className="ora-ui-text">Nothing matched {query ? <strong>“{query}”</strong> : "your search"}{filters.length ? <span> with filters: <strong>{filters.join(", ")}</strong></span> : ""}.</p>
    {suggestions.length > 0 && <p className="ora-ui-text">Try: {suggestions.join(" • ")}</p>}
    {onClearFilters && <button type="button" className="ora-ui-btn" onClick={onClearFilters}>Clear filters</button>}
  </section>);
}

export function FieldError({ id, message }) {
  return message ? <p className="ora-ui-error-text" id={id} role="alert">{message}</p> : null;
}

export function WarningState({ title, consequences, confirmLabel = "Delete", onConfirm, onCancel }) {
  return (<section className="ora-ui-card" data-testid="ora-warning-state" role="alertdialog" aria-label={title}>
    <span className="ora-ui-badge ora-ui-badge-warn">⚠ Warning</span>
    <h2 className="ora-ui-title" style={{ marginTop: 10 }}>{title}</h2>
    <p className="ora-ui-text">{consequences}</p>
    <div className="ora-ui-row">
      <button type="button" className="ora-ui-btn ora-ui-btn-danger" onClick={onConfirm}>{confirmLabel}</button>
      <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onCancel}>Cancel</button>
    </div>
  </section>);
}
export function OfflineBanner({ onRetry }) {
  return (<div className="ora-ui-banner ora-ui-banner-warn" data-testid="ora-offline-banner" role="status">
    <span><strong>You are offline.</strong> Saved content is still readable and your drafts are kept on this device.</span>
    {onRetry && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onRetry}>Try again</button>}
  </div>);
}

export function ReconnectingBanner({ attempt = 1, total = 5, onRetry }) {
  return (<div className="ora-ui-banner ora-ui-banner-warn" data-testid="ora-reconnecting-banner" role="status" aria-live="polite">
    <span>Reconnecting… Attempt {attempt} of {total}. Your work is safe.</span>
    {onRetry && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onRetry}>Retry now</button>}
  </div>);
}

export function SessionExpiredBanner({ email, onSignIn, resumeLabel = "Sign in again" }) {
  return (<section className="ora-ui-card" data-testid="ora-session-expired" role="alert">
    <div className="ora-ui-icon" aria-hidden="true">🔑</div>
    <h2 className="ora-ui-title">Your session has expired</h2>
    <p className="ora-ui-text">For your security, please sign in again. {email ? "Your email " + email + " is already filled in, " : ""}and you will return to the exact page you were on.</p>
    <button type="button" className="ora-ui-btn" onClick={onSignIn}>{resumeLabel}</button>
  </section>);
}

export function UnsavedChangesDialog({ itemName, onSave, onDiscard, onStay }) {
  return (<div className="ora-ui-dialog-overlay" data-testid="ora-unsaved-changes" role="presentation">
    <div className="ora-ui-dialog" role="alertdialog" aria-label="Unsaved changes">
      <h2 className="ora-ui-title">You have unsaved changes</h2>
      <p className="ora-ui-text">Changes to <strong>{itemName}</strong> have not been saved yet. If you leave now, those changes will be lost.</p>
      <div className="ora-ui-row">
        <button type="button" className="ora-ui-btn" onClick={onSave}>Save and continue</button>
        <button type="button" className="ora-ui-btn ora-ui-btn-danger" onClick={onDiscard}>Discard changes</button>
        <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onStay}>Stay on this page</button>
      </div>
    </div>
  </div>);
}

export function PartialSuccess({ done, total, failures = [], onRetryFailed }) {
  return (<section className="ora-ui-card" data-testid="ora-partial-success" role="status">
    <span className="ora-ui-badge ora-ui-badge-warn">Partial success</span>
    <h2 className="ora-ui-title" style={{ marginTop: 10 }}>{done} of {total} done</h2>
    {failures.length > 0 && <ul className="ora-ui-summary">{failures.map((f) => (<li key={f.name}><strong>{f.name}</strong>: {f.reason}</li>))}</ul>}
    {onRetryFailed && <button type="button" className="ora-ui-btn" onClick={onRetryFailed}>Retry only the failed items</button>}
  </section>);
}

export function DisabledButton({ disabled, hint, onClick, children }) {
  return (<span>
    <button type="button" className="ora-ui-btn" disabled={disabled} aria-disabled={disabled} onClick={onClick} style={disabled ? { opacity: 0.4 } : undefined}>{children}</button>
    {disabled && hint && <p className="ora-ui-text" style={{ marginTop: 6 }}>{hint}</p>}
  </span>);
}

export function ConfirmationDialog({ subject, consequence, confirmLabel, onConfirm, onCancel }) {
  return (<div className="ora-ui-dialog-overlay" data-testid="ora-confirmation-dialog" role="presentation">
    <div className="ora-ui-dialog" role="alertdialog" aria-label={confirmLabel}>
      <h2 className="ora-ui-title">{confirmLabel}: {subject}</h2>
      <p className="ora-ui-text">{consequence}</p>
      <div className="ora-ui-row">
        <button type="button" className="ora-ui-btn ora-ui-btn-danger" onClick={onConfirm}>{confirmLabel}</button>
        <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  </div>);
}

export function TimeoutState({ referenceId, onCheckStatus }) {
  return (<section className="ora-ui-card" data-testid="ora-timeout-state" role="alert">
    <h2 className="ora-ui-title">This request timed out</h2>
    <p className="ora-ui-text">Please do not try to pay again yet — you could be charged twice. Check the status of this request first{referenceId ? " (reference " + referenceId + ")" : ""}.</p>
    {onCheckStatus && <button type="button" className="ora-ui-btn" onClick={onCheckStatus}>Check status</button>}
  </section>);
}
export function ConflictState({ editorName, editedAt, mine, theirs, onMerge, onKeepMine, onSaveCopy }) {
  return (<section className="ora-ui-card" data-testid="ora-conflict-state" role="alert">
    <h2 className="ora-ui-title">This was edited by someone else</h2>
    <p className="ora-ui-text"><strong>{editorName}</strong> edited this {editedAt ? "at " + editedAt : "recently"}. Nothing has been overwritten. Choose how to continue.</p>
    <div className="ora-ui-diff">
      <div><strong>Your version</strong>
{mine}</div>
      <div><strong>{editorName} version</strong>
{theirs}</div>
    </div>
    <div className="ora-ui-row" style={{ marginTop: 12 }}>
      {onMerge && <button type="button" className="ora-ui-btn" onClick={onMerge}>Merge both versions</button>}
      {onKeepMine && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onKeepMine}>Keep my version</button>}
      {onSaveCopy && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onSaveCopy}>Save as a copy</button>}
    </div>
  </section>);
}

export function FileTransferList({ files = [], onCancel, onRetry }) {
  return (<section className="ora-ui-card" data-testid="ora-file-transfer-list" aria-label="File transfers">
    <h2 className="ora-ui-title">Files</h2>
    {files.map((f) => (<div key={f.id} style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
      {f.thumb && <img src={f.thumb} alt={""} width="48" height="48" style={{ borderRadius: 8, objectFit: "cover" }} />} 
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 14, fontWeight: 700 }}>{f.name} — {f.percent}% — {formatBytes(f.size)}</div>
        <div className="ora-ui-progress" role="progressbar" aria-valuenow={f.percent} aria-valuemin={0} aria-valuemax={100} aria-label={f.name}><div style={{ width: f.percent + "%" }} /></div>
        {f.status === "failed" && <span className="ora-ui-error-text">Upload failed. You can retry this file.</span>}
      </div>
      {f.status === "failed" && onRetry && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={() => onRetry(f.id)}>Retry</button>}
      {f.status !== "done" && onCancel && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={() => onCancel(f.id)}>Cancel</button>}
    </div>))}
  </section>);
}

export function BackgroundProcessing({ title, estimatedMinutes, onViewStatus }) {
  return (<section className="ora-ui-card" data-testid="ora-background-processing" role="status">
    <h2 className="ora-ui-title">{title}</h2>
    <p className="ora-ui-text">This will keep running in the background. You can leave this page or navigate away — {estimatedMinutes ? "about " + estimatedMinutes + " minutes left. " : ""}We will notify you by email and in the notification bell when it is done.</p>
    {onViewStatus && <button type="button" className="ora-ui-btn" onClick={onViewStatus}>View status</button>}
  </section>);
}

export function CancelledState({ title = "Cancelled", message, onRestart }) {
  return (<section className="ora-ui-card" data-testid="ora-cancelled-state" role="status">
    <h2 className="ora-ui-title">{title}</h2>
    <p className="ora-ui-text">{message || "This was cancelled. Nothing was changed and no charges were made."}</p>
    {onRestart && <button type="button" className="ora-ui-btn" onClick={onRestart}>Start again</button>}
  </section>);
}

export function MaintenanceState({ returnTimeUtc, returnTimeLocal, statusHref = "/support" }) {
  return (<section className="ora-ui-card" data-testid="ora-maintenance-state" role="status">
    <div className="ora-ui-icon" aria-hidden="true">🛠️</div>
    <h2 className="ora-ui-title">Ora is down for maintenance</h2>
    <p className="ora-ui-text">We are doing planned maintenance to keep Ora reliable. {returnTimeUtc ? "We expect to be back at " + returnTimeUtc + " UTC" : ""}{returnTimeLocal ? " (" + returnTimeLocal + " local time)" : ""}. Your work is saved.</p>
    <a className="ora-ui-link" href={statusHref}>Check the status page</a>
  </section>);
}

export function FirstTimeChecklist({ name, steps = [], onStart, onSkip }) {
  return (<section className="ora-ui-card" data-testid="ora-first-time-checklist" role="status">
    <h2 className="ora-ui-title">Welcome{name ? ", " + name : ""}</h2>
    <p className="ora-ui-text">Here is a short way to get started — just three steps.</p>
    <ol className="ora-ui-checklist">{steps.map((s, i) => (<li key={i}><span className="ora-ui-badge">{i + 1}</span>{s}</li>))}</ol>
    <div className="ora-ui-row">
      {onStart && <button type="button" className="ora-ui-btn" onClick={onStart}>Get started</button>}
      {onSkip && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onSkip}>Skip for now</button>}
    </div>
  </section>);
}

export function CompletionState({ title, summary, onGoToDashboard }) {
  return (<section className="ora-ui-card" data-testid="ora-completion-state" role="status">
    <span className="ora-ui-badge ora-ui-badge-success">🎉 Done</span>
    <h2 className="ora-ui-title" style={{ marginTop: 10 }}>{title}</h2>
    <p className="ora-ui-text">{summary}</p>
    {onGoToDashboard && <button type="button" className="ora-ui-btn" onClick={onGoToDashboard}>Go to dashboard</button>}
  </section>);
}
export function OptimisticToggle({ checked, onChange, label }) {
  return (<button type="button" role="switch" aria-checked={checked} aria-label={label} className="ora-ui-btn ora-ui-btn-secondary" onClick={() => onChange(!checked)}>{label}: {checked ? "On" : "Off"}</button>);
}

export function UndoToast({ itemName, onUndo, duration = 8000 }) {
  const [visible, setVisible] = useState(true);
  const [secondsLeft, setSecondsLeft] = useState(Math.ceil(duration / 1000));
  useEffect(() => {
    const hide = setTimeout(() => setVisible(false), duration);
    const tick = setInterval(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => { clearTimeout(hide); clearInterval(tick); };
  }, [duration]);
  if (visible === false) return null;
  return (<div className="ora-ui-toast" data-testid="ora-undo-toast" role="status">
    <span>{itemName} removed. Undo available for {secondsLeft}s.</span>
    <button type="button" className="ora-ui-btn" onClick={() => { setVisible(false); if (onUndo) onUndo(); }}>Undo</button>
  </div>);
}

export function useUndoToast() {
  const [toast, setToast] = useState(null);
  const showUndo = (itemName, onUndo) => setToast({ itemName, onUndo, key: Date.now() });
  const clearUndo = () => setToast(null);
  return { toast, showUndo, clearUndo };
}

export function RateLimitState({ retryInSeconds = 45, onUnlockAlternative, alternativeLabel = "Verify another way" }) {
  const [secondsLeft, setSecondsLeft] = useState(retryInSeconds);
  useEffect(() => {
    if (secondsLeft <= 0) return undefined;
    const tick = setInterval(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(tick);
  }, [secondsLeft]);
  return (<section className="ora-ui-card" data-testid="ora-rate-limit-state" role="status" aria-live="polite">
    <h2 className="ora-ui-title">Too many requests</h2>
    {secondsLeft > 0 ? (<p className="ora-ui-text">You have hit the rate limit for this action. You can try again in {formatCountdown(secondsLeft)} (Retry in {secondsLeft}s).</p>) : (<p className="ora-ui-text">The limit has lifted. You can try again now.</p>)}
    {onUnlockAlternative && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onUnlockAlternative}>{alternativeLabel}</button>}
  </section>);
}

export function QueueState({ position, total, estimatedMinutes }) {
  return (<section className="ora-ui-card" data-testid="ora-queue-state" role="status" aria-live="polite">
    <h2 className="ora-ui-title">You are in the queue</h2>
    <p className="ora-ui-text">You are <strong>#{position} of {total}</strong> in the queue{estimatedMinutes ? " — about " + estimatedMinutes + " minutes to wait" : ""}. Please stay on this page; your place is saved and will be kept if you refresh.</p>
  </section>);
}

export function StaleDataNotice({ lastUpdatedMinutes, onRefresh, critical }) {
  return (<div className="ora-ui-card" data-testid="ora-stale-data" role="status" style={{ padding: 12 }}>
    <span>Last updated {lastUpdatedMinutes} mins ago. {critical ? "This data is stale — treat it as out of date until refreshed." : "This may not be the latest data."}</span>
    {onRefresh && <div className="ora-ui-row" style={{ marginTop: 10 }}><button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onRefresh}>Refresh</button></div>}
  </div>);
}

export function SyncIndicator({ status = "synced", lastSynced, onRetry }) {
  const label = status === "syncing" ? "Syncing…" : status === "error" ? "Sync error" : "Synced";
  const detail = status === "syncing" ? "Saved locally, syncing to the cloud." : status === "error" ? "Saved locally only. Your local copy is safe." : "Saved to the cloud.";
  return (<div data-testid="ora-sync-indicator" role="status" aria-live="polite" style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
    <span className={"ora-ui-badge " + (status === "error" ? "ora-ui-badge-error" : status === "syncing" ? "ora-ui-badge-warn" : "ora-ui-badge-success")}>{label}</span>
    <span className="ora-ui-text" style={{ margin: 0 }}>{detail}{lastSynced ? " Last synced " + lastSynced + "." : ""}</span>
    {status === "error" && onRetry && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onRetry}>Retry sync</button>}
  </div>);
}
export function MultiStepForm({ step, total, steps = [], onBack, children }) {
  return (<section data-testid="ora-multi-step-form" aria-label={"Step " + step + " of " + total}>
    <div className="ora-ui-steps"><span>Step {step} of {total}</span>{steps.map((s, i) => (<span key={i} className={"ora-ui-step-dot " + (i + 1 === step ? "ora-ui-step-dot-active" : "")} aria-current={i + 1 === step ? "step" : undefined}>{i + 1}</span>))}</div>
    <p className="ora-ui-text">Your answers on each step are saved automatically. Going back keeps everything you entered.</p>
    {children}
    {step > 1 && onBack && <div className="ora-ui-row" style={{ marginTop: 12 }}><button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onBack}>Back</button></div>}
  </section>);
}

export function UpdateRequired({ mandatory, version, highlights = [], storeHref, onDismiss }) {
  return (<section className="ora-ui-card" data-testid="ora-update-required" role={mandatory ? "alert" : "status"}>
    <h2 className="ora-ui-title">{mandatory ? "Update required to continue" : "A new version is available"}</h2>
    <p className="ora-ui-text">Ora {version ? "version " + version : "a newer version"} is available. {mandatory ? "You need to update before you can keep using Ora." : "You can keep using Ora and update when it suits you."}</p>
    {highlights.length > 0 && <ul className="ora-ui-summary">{highlights.map((h, i) => (<li key={i}>{h}</li>))}</ul>}
    <div className="ora-ui-row">
      {storeHref && <a className="ora-ui-btn" href={storeHref}>Update Ora</a>}
      {mandatory === false && onDismiss && <button type="button" className="ora-ui-btn ora-ui-btn-secondary" onClick={onDismiss}>Not now</button>}
    </div>
  </section>);
}

export function useOnlineStatus() {
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  useEffect(() => {
    const goOnline = () => setOnline(true);
    const goOffline = () => setOnline(false);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => { window.removeEventListener("online", goOnline); window.removeEventListener("offline", goOffline); };
  }, []);
  return online;
}

export function AppStateBanners({ sessionEmail, onSignIn }) {
  const online = useOnlineStatus();
  const [sessionExpired, setSessionExpired] = useState(null);
  useEffect(() => {
    const handler = (event) => setSessionExpired(event.detail || {});
    window.addEventListener("ora:session-expired", handler);
    return () => window.removeEventListener("ora:session-expired", handler);
  }, []);
  if (sessionExpired) {
    return <SessionExpiredBanner email={sessionExpired.email || sessionEmail} onSignIn={() => { setSessionExpired(null); if (onSignIn) onSignIn(); }} />;
  }
  if (online === false) return <OfflineBanner />;
  return null;
}
