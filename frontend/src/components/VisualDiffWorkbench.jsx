/**
 * VisualDiffWorkbench.jsx — 2026-09-12
 *
 * Phase 3 + 4 orchestrator: patch generation -> isolated preview ->
 * before/after + diff -> action bar (Approve & Create PR / Refine /
 * Discard). Mounted in place of VisualBugAnalysisCard once the user
 * clicks "Generate fix" there.
 *
 * Fail-Safe Degradation: a preview failure never hides the diff —
 * `phase === "preview_failed"` still renders CodeDiffViewer, just
 * with a warning banner instead of BeforeAfterViewer.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, X, GitPullRequest, RefreshCw, Wand2, AlertTriangle, ExternalLink } from "lucide-react";
import { API_BASE } from "../lib/api";
import { BeforeAfterViewer } from "./BeforeAfterViewer";
import { CodeDiffViewer } from "./CodeDiffViewer";

const authedFetch = (url, token, opts = {}) => fetch(url, {
  signal: AbortSignal.timeout(120000),
  ...opts,
  headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers || {}) },
});

export const VisualDiffWorkbench = ({ reportId, token, onClose, onTokenLimit }) => {
  const [phase, setPhase] = useState("generating_patch");
  const [patch, setPatch] = useState(null);       // {summary, commit_message, files, has_syntax_errors}
  const [error, setError] = useState(null);
  const [refineOpen, setRefineOpen] = useState(false);
  const [refineText, setRefineText] = useState("");
  const [prUrl, setPrUrl] = useState(null);
  const [coldInstall, setColdInstall] = useState(false);
  const pollRef = useRef(null);

  const authedUrl = (variant) => `${API_BASE}/visual-bugfix/${reportId}/image?variant=${variant}&auth=${encodeURIComponent(token || "")}`;

  const runGeneratePatch = useCallback(async (refineInstruction) => {
    setPhase("generating_patch");
    setError(null);
    try {
      const res = await authedFetch(`${API_BASE}/visual-bugfix/${reportId}/generate-patch`, token, {
        method: "POST", body: JSON.stringify(refineInstruction ? { refine_instruction: refineInstruction } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 402) {
        const detail = data?.detail || data;
        onTokenLimit?.(detail);
        setError(detail?.message || "You've reached your plan's task limit.");
        setPhase("error");
        return;
      }
      if (!res.ok || !data.ok) throw new Error(data.error || data.detail || `Patch generation failed (${res.status})`);
      setPatch(data);
      setPhase("patch_ready");
    } catch (e) {
      setError(e?.message || String(e));
      setPhase("error");
    }
  }, [reportId, token, onTokenLimit]);

  useEffect(() => { runGeneratePatch(); }, [runGeneratePatch]);

  const stopPolling = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  useEffect(() => () => stopPolling(), []);

  const runPreview = useCallback(async () => {
    setPhase("preview_generating");
    setError(null);
    try {
      const res = await authedFetch(`${API_BASE}/visual-bugfix/${reportId}/preview`, token, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.detail || data.error || `Preview failed to start (${res.status})`);
    } catch (e) {
      setError(e?.message || String(e));
      setPhase("preview_failed");
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const res = await authedFetch(`${API_BASE}/visual-bugfix/${reportId}/status`, token);
        const data = await res.json().catch(() => ({}));
        setColdInstall(!!data.cold_install);
        if (data.status === "previewed") { stopPolling(); setPhase("previewed"); }
        else if (data.status === "preview_failed") { stopPolling(); setError(data.preview_error); setPhase("preview_failed"); }
      } catch { /* transient poll error — try again next tick */ }
    }, 2000);
  }, [reportId, token]);

  const runApprovePr = useCallback(async () => {
    setPhase("pr_creating");
    setError(null);
    try {
      const res = await authedFetch(`${API_BASE}/visual-bugfix/${reportId}/approve-pr`, token, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.error || data.detail || `Could not create PR (${res.status})`);
      setPrUrl(data.pr_url);
      setPhase("pr_created");
    } catch (e) {
      setError(e?.message || String(e));
      setPhase("patch_ready");
    }
  }, [reportId, token]);

  const submitRefine = () => {
    if (!refineText.trim()) return;
    setRefineOpen(false);
    runGeneratePatch(refineText.trim());
    setRefineText("");
  };

  return (
    <div data-testid="visual-diff-workbench" style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, color: "#FF6608" }}>
          <Wand2 size={14} /> Fix workbench
        </div>
        <button type="button" data-testid="visual-diff-workbench-dismiss" onClick={onClose}
                aria-label="Dismiss" style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-dim)" }}>
          <X size={14} />
        </button>
      </div>

      {(phase === "generating_patch") && (
        <div data-testid="visual-diff-workbench-loading" style={loadingStyle}>
          <Loader2 size={14} className="animate-spin" /> Generating fix…
        </div>
      )}

      {phase === "error" && (
        <div data-testid="visual-diff-workbench-error" style={errorStyle}><AlertTriangle size={14} /> {error}</div>
      )}

      {patch && phase !== "generating_patch" && phase !== "error" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {patch.summary && <div style={{ fontSize: 13, opacity: 0.85 }}>{patch.summary}</div>}

          {phase === "preview_generating" && (
            <div data-testid="visual-diff-workbench-preview-loading" style={loadingStyle}>
              <Loader2 size={14} className="animate-spin" />
              {coldInstall ? "Building dependency cache — first run only, may take a bit…" : "Building isolated preview…"}
            </div>
          )}

          {phase === "preview_failed" && (
            <div data-testid="visual-diff-workbench-preview-warning" style={errorStyle}>
              <AlertTriangle size={14} /> Preview unavailable — showing the code diff below. ({error || "unknown error"})
            </div>
          )}

          {phase === "previewed" && (
            <BeforeAfterViewer beforeUrl={authedUrl("before")} afterUrl={authedUrl("after")} />
          )}

          {phase === "pr_created" && prUrl && (
            <a data-testid="visual-diff-workbench-pr-link" href={prUrl} target="_blank" rel="noreferrer" style={prLinkStyle}>
              <ExternalLink size={13} /> Draft PR opened — review on GitHub
            </a>
          )}

          <CodeDiffViewer files={patch.files} />

          {refineOpen && (
            <div style={{ display: "flex", gap: 6 }}>
              <input
                data-testid="visual-diff-workbench-refine-input"
                value={refineText}
                onChange={(e) => setRefineText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submitRefine()}
                placeholder="What should change about this fix?"
                style={refineInputStyle}
              />
              <button type="button" data-testid="visual-diff-workbench-refine-submit" onClick={submitRefine} style={smallBtnStyle}>Send</button>
            </div>
          )}

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" data-testid="visual-diff-workbench-preview-btn" onClick={runPreview}
                    disabled={patch.has_syntax_errors || phase === "preview_generating"} style={primaryBtnStyle}>
              Preview fix
            </button>
            <button type="button" data-testid="visual-diff-workbench-approve-pr-btn" onClick={runApprovePr}
                    disabled={patch.has_syntax_errors || phase === "pr_creating" || phase === "pr_created"} style={primaryBtnStyle}>
              <GitPullRequest size={13} /> {phase === "pr_creating" ? "Creating PR…" : "Approve & Create PR"}
            </button>
            <button type="button" data-testid="visual-diff-workbench-refine-btn" onClick={() => setRefineOpen((o) => !o)} style={smallBtnStyle}>
              <RefreshCw size={13} /> Refine fix
            </button>
            <button type="button" data-testid="visual-diff-workbench-discard-btn" onClick={onClose} style={smallBtnStyle}>Discard</button>
          </div>
        </div>
      )}
    </div>
  );
};

const cardStyle = { margin: "8px 0", padding: "14px 16px", borderRadius: 12, background: "rgba(255,102,8,0.06)", border: "1px solid rgba(255,102,8,0.25)", fontSize: 13 };
const loadingStyle = { display: "flex", alignItems: "center", gap: 8, color: "var(--text-dim)" };
const errorStyle = { display: "flex", alignItems: "center", gap: 8, color: "#ef4444" };
const primaryBtnStyle = { display: "flex", alignItems: "center", gap: 6, padding: "6px 14px", borderRadius: 999, background: "#FF6608", border: "none", color: "#0a0a0a", fontWeight: 600, fontSize: 12, cursor: "pointer" };
const smallBtnStyle = { display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", borderRadius: 999, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)", color: "var(--text-primary)", fontSize: 12, cursor: "pointer" };
const refineInputStyle = { flex: 1, padding: "6px 10px", borderRadius: 8, background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.15)", color: "var(--text-primary)", fontSize: 12 };
const prLinkStyle = { display: "flex", alignItems: "center", gap: 6, color: "#4ade80", fontSize: 12, textDecoration: "none" };

export default VisualDiffWorkbench;
