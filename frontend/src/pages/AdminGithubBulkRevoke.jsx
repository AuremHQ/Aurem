/**
 * pages/AdminGithubBulkRevoke.jsx — 2026-08-30
 *
 * Admin tool: bulk-select GitHub App installations -> force-uninstall,
 * or (non-destructively) flag idle-but-working connections for
 * re-engage. Built after a near-miss where an admin almost revoked
 * 27 of 28 "never ran a task" users whose connection was actually
 * fine — pat_status is visible + filterable BEFORE selection, and
 * a typed "REVOKE" confirm is required whenever a working connection
 * is in the selection.
 *
 * STANDING GATE — real revoke is disabled until the live drill-repo
 * verify (U1-U6) is confirmed. See
 * /app/memory/GITHUB_BULK_REVOKE_DRILL_VERIFY.md. The "Revoke"
 * button stays disabled (server also refuses with 403) until the
 * `github_bulk_revoke_live_verified` feature flag is flipped ON.
 *
 * Backend: GET  /admin/github/connections
 *          POST /admin/github/bulk-revoke   (dry_run + real)
 *          POST /admin/github/flag-idle
 */
import React, { useState, useEffect, useCallback, useMemo } from "react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import {
  RefreshCw, ShieldAlert, Flag, AlertTriangle, Loader2, X, Check, Eraser,
} from "lucide-react";

const PAT_BADGE = {
  valid:          { bg: "#15803d20", fg: "#4ade80", label: "Valid" },
  invalid:        { bg: "#dc262620", fg: "#f87171", label: "Invalid" },
  repo_not_found: { bg: "#dc262620", fg: "#f87171", label: "Repo 404" },
  missing:        { bg: "#f59e0b20", fg: "#fbbf24", label: "No token" },
  probe_error:    { bg: "#f59e0b20", fg: "#fbbf24", label: "Probe fail" },
};

function PATBadge({ status }) {
  const b = PAT_BADGE[status] || { bg: "#37415120", fg: "#9ca3af", label: status || "-" };
  return (
    <span data-testid={"gbr-pat-badge-" + status}
          style={{
            padding: "2px 8px", borderRadius: 3, fontSize: 10,
            fontFamily: "'JetBrains Mono', monospace",
            background: b.bg, color: b.fg, border: "1px solid " + b.fg + "30",
          }}>{b.label}</span>
  );
}

const VIEWS = [
  { id: "revokable", label: "Revokable (broken only)",
    hint: "Connection itself is broken (invalid token, uninstalled on GitHub's side, etc.)" },
  { id: "idle",      label: "Idle (working, unused)",
    hint: "Connection works fine, but 0 tasks run — candidates for re-engagement or cleanup" },
  { id: "stuck",     label: "Stuck / Never completed",
    hint: "Latest connect attempt ended in a denial or an unresolved redirect — no GitHub connection exists to revoke" },
  { id: "all",       label: "All" },
];

const STUCK_STAGE_BADGE = {
  app_install_denied:   { bg: "#dc262620", fg: "#f87171", label: "Denied" },
  app_install_redirect: { bg: "#f59e0b20", fg: "#fbbf24", label: "Redirected, no follow-up" },
};

function StuckStageBadge({ stage }) {
  const b = STUCK_STAGE_BADGE[stage] || { bg: "#37415120", fg: "#9ca3af", label: stage || "-" };
  return (
    <span data-testid={"gbr-stuck-stage-badge-" + stage}
          style={{
            padding: "2px 8px", borderRadius: 3, fontSize: 10,
            fontFamily: "'JetBrains Mono', monospace",
            background: b.bg, color: b.fg, border: "1px solid " + b.fg + "30",
          }}>{b.label}</span>
  );
}

const MAX_REVOKE_BATCH = 10;

export default function AdminGithubBulkRevoke() {
  const [view, setView] = useState("revokable");
  const [rows, setRows] = useState([]);
  // 2026-09-07 bug fix — `rows` alone isn't enough to know whether
  // it's safe to render: on a tab switch, React can commit a render
  // where `view` has already changed but `loading`/`rows` still hold
  // the PREVIOUS tab's values (the `useEffect` that kicks off the new
  // fetch + `setLoading(true)` only runs AFTER that render commits).
  // That one frame used to show the wrong tab's rows. `rowsView`
  // tracks which `view` the current `rows` actually belong to, so the
  // render check below can catch that frame and show the loading
  // state instead — "don't render any rows for a tab until that
  // tab's filtered fetch resolves."
  const [rowsView, setRowsView] = useState(null);
  const [loading, setLoading] = useState(true);
  const [liveVerified, setLiveVerified] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [preview, setPreview] = useState(null);
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [reason, setReason] = useState("");
  // Stuck / Never Completed tab — separate state since its rows are
  // user-shaped (no installation, no PAT, no repo), not the
  // installation-shaped rows the revokable/idle/all tabs use.
  const [stuckRows, setStuckRows] = useState([]);
  const [selectedStuck, setSelectedStuck] = useState(new Set());
  const [clearReason, setClearReason] = useState("");
  const [clearResult, setClearResult] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setSelected(new Set());
    setSelectedStuck(new Set());
    setPreview(null);
    setResult(null);
    setClearResult(null);
    try {
      if (view === "stuck") {
        const r = await api.get("/admin/github/stuck-connections");
        setStuckRows(r.data.rows || []);
        setRowsView(view);
      } else {
        const r = await api.get("/admin/github/connections", { params: { view } });
        setRows(r.data.rows || []);
        setRowsView(view);
        setLiveVerified(!!r.data.live_verified);
      }
    } catch {
      if (view === "stuck") setStuckRows([]);
      else setRows([]);
      setRowsView(view);
      toast({ message: "Failed to load GitHub connections", kind: "error" });
    } finally {
      setLoading(false);
    }
  }, [view]);

  useEffect(() => { load(); }, [load]);

  const selectableRows = useMemo(
    () => rows.filter((r) => r.installation_id != null), [rows],
  );

  const toggleAllFiltered = () => {
    if (selected.size === selectableRows.length && selectableRows.length > 0) {
      setSelected(new Set());
    } else {
      setSelected(new Set(selectableRows.map((r) => r.installation_id)));
    }
  };

  const toggleOne = (iid) => {
    setSelected((cur) => {
      const next = new Set(cur);
      next.has(iid) ? next.delete(iid) : next.add(iid);
      return next;
    });
  };

  const startDryRun = async () => {
    if (selected.size === 0) return;
    if (selected.size > MAX_REVOKE_BATCH) {
      toast({ message: `Max ${MAX_REVOKE_BATCH} per batch — deselect some rows to continue.`, kind: "error" });
      return;
    }
    if (!reason.trim()) {
      toast({ message: "A reason is required before revoking (shows in the audit log).", kind: "error" });
      return;
    }
    setBusy(true);
    try {
      const r = await api.post("/admin/github/bulk-revoke", {
        installation_ids: [...selected], dry_run: true,
      });
      setPreview(r.data);
      setConfirmText("");
    } catch (e) {
      toast({ message: (e?.response?.data?.detail?.message) || "Preview failed", kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const confirmRevoke = async () => {
    setBusy(true);
    try {
      const r = await api.post("/admin/github/bulk-revoke", {
        installation_ids: [...selected], confirm_text: confirmText, reason,
      });
      setResult(r.data);
      setPreview(null);
      setSelected(new Set());
      toast({ message: r.data.summary, kind: "success" });
      load();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      toast({ message: (detail && detail.message) || "Bulk revoke failed", kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const flagIdle = async () => {
    if (selected.size === 0) return;
    setBusy(true);
    try {
      const r = await api.post("/admin/github/flag-idle", {
        installation_ids: [...selected], reason,
      });
      toast({ message: "Flagged " + r.data.flagged + " for re-engage", kind: "success" });
      setSelected(new Set());
      load();
    } catch {
      toast({ message: "Flag failed", kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const clearableStuck = useMemo(
    () => stuckRows.filter((r) => (r.pending_oauth_states || 0) > 0), [stuckRows],
  );

  const toggleAllStuck = () => {
    if (selectedStuck.size === clearableStuck.length && clearableStuck.length > 0) {
      setSelectedStuck(new Set());
    } else {
      setSelectedStuck(new Set(clearableStuck.map((r) => r.user_id)));
    }
  };

  const toggleOneStuck = (uid) => {
    setSelectedStuck((cur) => {
      const next = new Set(cur);
      next.has(uid) ? next.delete(uid) : next.add(uid);
      return next;
    });
  };

  const clearStuckState = async () => {
    if (selectedStuck.size === 0) return;
    setBusy(true);
    try {
      const r = await api.post("/admin/github/clear-stuck-state", {
        user_ids: [...selectedStuck], reason: clearReason,
      });
      setClearResult(r.data);
      toast({
        message: `Cleared ${r.data.cleared_rows} stale token(s) for ${r.data.users_processed} user(s) — no effect on reconnect-ability`,
        kind: "success",
      });
      setSelectedStuck(new Set());
      load();
    } catch (e) {
      toast({ message: (e?.response?.data?.detail?.message) || "Clear failed", kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const showingStaleOrLoading = loading || rowsView !== view;

  const needsTypedConfirm = preview && (preview.valid_count > 0 || preview.total > 5);
  const canExecute = preview && (!needsTypedConfirm || confirmText.trim().toUpperCase() === "REVOKE");
  const overBatchCap = selected.size > MAX_REVOKE_BATCH;
  const reasonMissingForRevoke = !reason.trim();

  return (
    <div style={{ padding: "24px 20px", maxWidth: 1280 }} data-testid="gbr-page">
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0, color: "var(--text)" }}>
          GitHub Bulk Revoke
        </h1>
        <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>
          Select installations to force-uninstall, or flag idle-but-working
          connections for re-engage. Data preserved on revoke - never deleted.
        </div>
      </div>

      {!liveVerified && view !== "stuck" && (
        <div data-testid="gbr-not-verified-banner" style={{
          display: "flex", gap: 8, alignItems: "flex-start",
          background: "#f59e0b14", border: "1px solid #f59e0b40",
          borderRadius: 4, padding: "10px 12px", marginBottom: 14, fontSize: 11,
        }}>
          <AlertTriangle size={14} style={{ color: "#fbbf24", flexShrink: 0, marginTop: 1 }} />
          <div style={{ color: "var(--text-dim)" }}>
            <strong style={{ color: "#fbbf24" }}>Live verification pending.</strong>{" "}
            This tool is not yet cleared for real use - the drill-repo verify (real
            GitHub uninstall behavior) hasn't run against a disposable installation
            yet. "Revoke" is disabled below; "Flag idle" (non-destructive, zero
            GitHub calls) is unaffected.
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        {VIEWS.map((v) => (
          <button key={v.id}
                  data-testid={"gbr-view-" + v.id}
                  onClick={() => setView(v.id)}
                  title={v.hint || undefined}
                  style={{
                    padding: "6px 10px", borderRadius: 3, fontSize: 11, cursor: "pointer",
                    border: "1px solid " + (view === v.id ? "var(--accent)" : "var(--border)"),
                    background: view === v.id ? "var(--accent-soft)" : "var(--panel-2)",
                    color: view === v.id ? "var(--accent)" : "var(--text-dim)",
                    textAlign: "left", maxWidth: 220,
                  }}>
            <div>{v.label}</div>
            {v.hint && (
              <div data-testid={"gbr-view-hint-" + v.id} style={{
                fontSize: 9, marginTop: 2, fontWeight: 400, whiteSpace: "normal",
                color: view === v.id ? "var(--accent)" : "var(--text-faint)", opacity: 0.85,
              }}>{v.hint}</div>
            )}
          </button>
        ))}
        <button data-testid="gbr-refresh" onClick={load} disabled={loading}
                style={{
                  marginLeft: "auto", display: "flex", alignItems: "center", gap: 6,
                  padding: "6px 10px", borderRadius: 3, fontSize: 11, cursor: "pointer",
                  border: "1px solid var(--border)", background: "var(--panel-2)",
                  color: "var(--text-dim)",
                }}>
          <RefreshCw size={12} className={loading ? "spin" : ""} /> Refresh
        </button>
      </div>

      {view === "stuck" ? (
        <>
          <div data-testid="gbr-stuck-info-banner" style={{
            display: "flex", gap: 8, alignItems: "flex-start",
            background: "#38bdf814", border: "1px solid #38bdf840",
            borderRadius: 4, padding: "10px 12px", marginBottom: 14, fontSize: 11,
          }}>
            <AlertTriangle size={14} style={{ color: "#38bdf8", flexShrink: 0, marginTop: 1 }} />
            <div style={{ color: "var(--text-dim)" }}>
              <strong style={{ color: "#38bdf8" }}>No live GitHub connection exists for these users</strong> —
              their most recent connect attempt ended in a denial or an unresolved redirect, so
              there is nothing to "Revoke". "Clear stuck state" below deletes stale, never-consumed
              connect tokens (pure hygiene) — confirmed by code trace to have{" "}
              <strong style={{ color: "var(--text)" }}>no effect on the user's ability to reconnect</strong>;
              they can already retry right now with zero interference. Repeated denials are most
              likely GitHub-side (declined again, org-owner approval required, etc.), not an AUREM
              bug. The 2026-09-13 120s retry-window fix is unrelated — it covers a later stage
              (repo-access lag <em>after</em> a successful install), so no pre/post-fix split is
              shown here.
            </div>
          </div>

          <div style={{
            background: "var(--panel-2)", border: "1px solid var(--border)",
            borderRadius: 4, overflow: "hidden", marginBottom: 14,
          }}>
            <div style={{
              display: "grid",
              gridTemplateColumns: "28px 1fr 170px 130px 110px 90px",
              gap: 8, padding: "8px 12px", fontSize: 10, letterSpacing: "0.06em",
              textTransform: "uppercase", color: "var(--text-faint)",
              borderBottom: "1px solid var(--border)",
            }}>
              <input type="checkbox" data-testid="gbr-stuck-select-all"
                     checked={clearableStuck.length > 0 && selectedStuck.size === clearableStuck.length}
                     onChange={toggleAllStuck} />
              <span>Email</span><span>Last attempt</span><span>Attempts</span>
              <span>First attempt</span><span>Clearable</span>
            </div>
            {showingStaleOrLoading ? (
              <div style={{ padding: 20, color: "var(--text-faint)", fontSize: 11 }}>
                <Loader2 size={12} className="spin" style={{ marginRight: 6, verticalAlign: "middle" }} />
                Loading…
              </div>
            ) : stuckRows.length === 0 ? (
              <div style={{ padding: 20, color: "var(--text-faint)", fontSize: 11, fontStyle: "italic" }}>
                No stuck users — nobody's latest connect attempt ended in a denial or unresolved redirect.
              </div>
            ) : stuckRows.map((r) => (
              <div key={r.user_id}
                   data-testid={"gbr-stuck-row-" + r.user_id}
                   style={{
                     display: "grid",
                     gridTemplateColumns: "28px 1fr 170px 130px 110px 90px",
                     gap: 8, padding: "8px 12px", fontSize: 11, alignItems: "center",
                     borderTop: "1px solid var(--border)",
                   }}>
                <input type="checkbox" data-testid={"gbr-stuck-checkbox-" + r.user_id}
                       checked={selectedStuck.has(r.user_id)}
                       disabled={!(r.pending_oauth_states > 0)}
                       onChange={() => toggleOneStuck(r.user_id)} />
                <div>
                  <div style={{ color: "var(--text)" }}>{r.email || r.user_id}</div>
                  {r.reason && (
                    <div style={{ fontSize: 10, color: "var(--text-faint)" }}>reason: {r.reason}</div>
                  )}
                </div>
                <StuckStageBadge stage={r.stage} />
                <span style={{ color: "var(--text-dim)" }}>{r.redirect_attempts ?? 0}</span>
                <span style={{ fontSize: 10, color: "var(--text-faint)" }}>
                  {r.first_attempt_at ? new Date(r.first_attempt_at).toLocaleDateString() : "—"}
                </span>
                <span data-testid={"gbr-stuck-clearable-" + r.user_id}
                      style={{ fontSize: 10, color: r.pending_oauth_states > 0 ? "#4ade80" : "var(--text-faint)" }}>
                  {r.pending_oauth_states > 0 ? `${r.pending_oauth_states} token(s)` : "none"}
                </span>
              </div>
            ))}
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
            <input
              data-testid="gbr-clear-reason-input"
              type="text" placeholder="Reason (optional, shows in the audit log)…"
              value={clearReason} onChange={(e) => setClearReason(e.target.value)}
              style={{
                flex: "0 0 300px", padding: "6px 8px", background: "var(--panel-2)",
                border: "1px solid var(--border)", color: "var(--text)", fontSize: 11, borderRadius: 3,
              }}
            />
            <span style={{ fontSize: 11, color: "var(--text-faint)" }}>
              {selectedStuck.size} selected
            </span>
            <button data-testid="gbr-clear-stuck-btn"
                    onClick={clearStuckState}
                    disabled={busy || selectedStuck.size === 0}
                    title='Deletes stale, never-consumed connect tokens only — no effect on the user&#39;s ability to reconnect'
                    style={{
                      display: "flex", alignItems: "center", gap: 6,
                      padding: "7px 12px", borderRadius: 3, fontSize: 11, cursor: "pointer",
                      border: "1px solid #38bdf840", background: "#38bdf814", color: "#38bdf8",
                      opacity: selectedStuck.size === 0 ? 0.5 : 1,
                    }}>
              {busy ? <Loader2 size={12} className="spin" /> : <Eraser size={12} />}
              Clear stuck state (hygiene only)
            </button>
          </div>

          {clearResult && (
            <div data-testid="gbr-clear-result-summary" style={{
              background: "var(--bg-elev)", border: "1px solid var(--border)",
              borderRadius: 6, padding: 14, fontSize: 12, color: "var(--text)",
            }}>
              Cleared {clearResult.cleared_rows} stale token(s) for {clearResult.users_processed} user(s).
              <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>{clearResult.note}</div>
            </div>
          )}
        </>
      ) : (
      <>
      <div style={{
        background: "var(--panel-2)", border: "1px solid var(--border)",
        borderRadius: 4, overflow: "hidden", marginBottom: 14,
      }}>
        <div style={{
          display: "grid",
          gridTemplateColumns: "28px 1fr 90px 90px 100px 70px 90px 90px",
          gap: 8, padding: "8px 12px", fontSize: 10, letterSpacing: "0.06em",
          textTransform: "uppercase", color: "var(--text-faint)",
          borderBottom: "1px solid var(--border)",
        }}>
          <input type="checkbox" data-testid="gbr-select-all"
                 checked={selectableRows.length > 0 && selected.size === selectableRows.length}
                 onChange={toggleAllFiltered} />
          <span>Email / Repo</span><span>Status</span><span>PAT</span>
          <span>Tasks</span><span>In-flight</span><span>Last session</span><span>Flagged</span>
        </div>
        {showingStaleOrLoading ? (
          <div style={{ padding: 20, color: "var(--text-faint)", fontSize: 11 }}>
            <Loader2 size={12} className="spin" style={{ marginRight: 6, verticalAlign: "middle" }} />
            Loading…
          </div>
        ) : rows.length === 0 ? (
          <div style={{ padding: 20, color: "var(--text-faint)", fontSize: 11, fontStyle: "italic" }}>
            No rows match this filter.
          </div>
        ) : rows.map((r) => (
          <div key={`${r.installation_id ?? "no_install"}-${r.user_id}`}
               data-testid={"gbr-row-" + r.installation_id}
               style={{
                 display: "grid",
                 gridTemplateColumns: "28px 1fr 90px 90px 100px 70px 90px 90px",
                 gap: 8, padding: "8px 12px", fontSize: 11, alignItems: "center",
                 borderTop: "1px solid var(--border)",
               }}>
            <input type="checkbox" data-testid={"gbr-checkbox-" + r.installation_id}
                   checked={selected.has(r.installation_id)}
                   disabled={r.installation_id == null}
                   onChange={() => toggleOne(r.installation_id)} />
            <div>
              <div style={{ color: "var(--text)" }}>{r.email || "—"}</div>
              <div style={{ fontSize: 10, color: "var(--text-faint)" }}>{r.repo || "—"}</div>
            </div>
            <span style={{ fontSize: 10, color: "var(--text-dim)", textTransform: "capitalize" }}>
              {r.status || "—"}
            </span>
            <PATBadge status={r.pat_status} />
            <span style={{ color: "var(--text-dim)" }}>{r.task_count ?? 0}</span>
            <span style={{ color: r.in_flight_work ? "#fbbf24" : "var(--text-faint)" }}>
              {r.in_flight_work ? "yes" : "—"}
            </span>
            <span style={{ fontSize: 10, color: "var(--text-faint)" }}>
              {r.last_session_at ? new Date(r.last_session_at).toLocaleDateString() : "never"}
            </span>
            <span style={{ fontSize: 10, color: r.re_engage_flagged ? "#4ade80" : "var(--text-faint)" }}>
              {r.re_engage_flagged ? "yes" : "—"}
            </span>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
        <input
          data-testid="gbr-reason-input"
          type="text" placeholder="Reason (required to revoke, optional to flag idle)…"
          value={reason} onChange={(e) => setReason(e.target.value)}
          style={{
            flex: "0 0 300px", padding: "6px 8px", background: "var(--panel-2)",
            border: "1px solid " + (reasonMissingForRevoke ? "var(--border)" : "#4ade8060"),
            color: "var(--text)", fontSize: 11, borderRadius: 3,
          }}
        />
        <span style={{ fontSize: 11, color: overBatchCap ? "#f87171" : "var(--text-faint)" }}>
          {selected.size} selected{overBatchCap ? ` (max ${MAX_REVOKE_BATCH} per revoke batch)` : ""}
        </span>
        <button data-testid="gbr-flag-idle-btn"
                onClick={flagIdle}
                disabled={busy || selected.size === 0}
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  padding: "7px 12px", borderRadius: 3, fontSize: 11, cursor: "pointer",
                  border: "1px solid #4ade8040", background: "#4ade8014", color: "#4ade80",
                  opacity: selected.size === 0 ? 0.5 : 1,
                }}>
          <Flag size={12} /> Flag idle (non-destructive)
        </button>
        <button data-testid="gbr-revoke-preview-btn"
                onClick={startDryRun}
                disabled={busy || selected.size === 0 || !liveVerified || overBatchCap}
                title={!liveVerified
                  ? "Live verification pending — this tool is not yet cleared for use"
                  : overBatchCap
                    ? `Max ${MAX_REVOKE_BATCH} per batch — deselect some rows`
                    : "Preview blast radius before revoking (reason required)"}
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  padding: "7px 12px", borderRadius: 3, fontSize: 11, cursor: "pointer",
                  border: "1px solid #f8717140", background: "#f8717114", color: "#f87171",
                  opacity: (selected.size === 0 || !liveVerified || overBatchCap) ? 0.5 : 1,
                }}>
          <ShieldAlert size={12} /> Revoke selected…
        </button>
      </div>

      {preview && (
        <div data-testid="gbr-preview-modal" style={{
          background: "var(--bg-elev)", border: "1px solid #f8717150",
          borderRadius: 6, padding: 16, marginBottom: 16,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <strong style={{ fontSize: 13, color: "var(--text)" }}>
              Blast radius — {preview.total} installation(s)
            </strong>
            <button data-testid="gbr-preview-close" onClick={() => setPreview(null)}
                    style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-faint)" }}>
              <X size={14} />
            </button>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 10 }}>
            This kills AUREM App access + all install tokens for these installations.
            GitHub branches/PRs remain but AUREM loses access — one-way, they must re-install.
            {" "}<strong style={{ color: "var(--text)" }}>
              This will uninstall the GitHub App for these users. They will need to
              reconnect if they return. Their project data is NOT deleted.
            </strong>
          </div>
          {preview.preview.map((p) => (
            <div key={p.installation_id} data-testid={"gbr-preview-row-" + p.installation_id}
                 style={{ fontSize: 11, padding: "4px 0", display: "flex", gap: 8, color: "var(--text-dim)" }}>
              <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{p.installation_id}</span>
              <span>{p.email || "—"}</span><span>{p.repo || "—"}</span>
              <PATBadge status={p.pat_status} />
            </div>
          ))}

          {needsTypedConfirm && (
            <div data-testid="gbr-hard-guard-warning" style={{
              display: "flex", gap: 8, alignItems: "flex-start",
              background: "#f8717118", border: "1px solid #f8717150",
              borderRadius: 4, padding: "10px 12px", marginTop: 12, fontSize: 11,
            }}>
              <AlertTriangle size={14} style={{ color: "#f87171", flexShrink: 0, marginTop: 1 }} />
              <div style={{ color: "var(--text-dim)" }}>
                <strong style={{ color: "#f87171" }}>
                  {preview.valid_count > 0
                    ? `${preview.valid_count} of your ${preview.total} selected users have a WORKING GitHub connection.`
                    : `${preview.total} installations selected — over the 5-account no-typing threshold.`}
                </strong>{" "}
                {preview.valid_count > 0
                  ? "Revoking will break something that currently works for them."
                  : "A misclick this size is exactly what this check exists to catch."}{" "}
                Type{" "}
                <code style={{ color: "var(--code-fg)", background: "var(--code-bg)", padding: "0 4px", borderRadius: 3 }}>REVOKE</code>{" "}
                below to proceed.
              </div>
            </div>
          )}

          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12 }}>
            {needsTypedConfirm && (
              <input data-testid="gbr-typed-confirm-input"
                     type="text" placeholder='Type "REVOKE" to confirm'
                     value={confirmText} onChange={(e) => setConfirmText(e.target.value)}
                     style={{
                       padding: "6px 8px", background: "var(--panel-2)",
                       border: "1px solid var(--border)", color: "var(--text)",
                       fontSize: 11, borderRadius: 3, flex: "0 0 200px",
                     }} />
            )}
            <button data-testid="gbr-confirm-revoke-btn"
                    onClick={confirmRevoke}
                    disabled={busy || !canExecute}
                    style={{
                      display: "flex", alignItems: "center", gap: 6,
                      padding: "7px 14px", borderRadius: 3, fontSize: 11, cursor: "pointer",
                      border: "1px solid #f87171", background: "#f8717125", color: "#f87171",
                      opacity: canExecute ? 1 : 0.5,
                    }}>
              {busy ? <Loader2 size={12} className="spin" /> : <Check size={12} />}
              Confirm revoke {preview.total} installation(s)
            </button>
          </div>
        </div>
      )}

      {result && (
        <div data-testid="gbr-result-summary" style={{
          background: "var(--bg-elev)", border: "1px solid var(--border)",
          borderRadius: 6, padding: 14, fontSize: 12, color: "var(--text)",
        }}>
          {result.summary}
        </div>
      )}
      </>
      )}
    </div>
  );
}
