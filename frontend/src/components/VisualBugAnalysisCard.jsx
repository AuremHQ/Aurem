/**
 * VisualBugAnalysisCard.jsx — 2026-09-12
 *
 * Multimodal Visual Bug-Fix pipeline, Phase 1+2 result card. Shown
 * inline above the composer (same convention as FirstScanCard /
 * PlanApprovalCard — a state-driven card, not a message-list entry)
 * while/after a "Find & fix this bug" screenshot analysis runs.
 *
 * Phase 3 (patch generation + headless preview) and Phase 4
 * (before/after comparison UI) are NOT built yet — this card only
 * shows the vision analysis + ranked candidate source files.
 */
import { Loader2, X, Search, FileCode, AlertTriangle, Wand2 } from "lucide-react";

export const VisualBugAnalysisCard = ({ result, onDismiss, onGenerateFix }) => {
  if (!result) return null;
  const { status, name, error } = result;

  return (
    <div
      data-testid="visual-bug-analysis-card"
      style={{
        margin: "8px 0",
        padding: "14px 16px",
        borderRadius: 12,
        background: "rgba(255,102,8,0.06)",
        border: "1px solid rgba(255,102,8,0.25)",
        fontSize: 13,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, color: "#FF6608" }}>
          <Search size={14} />
          Visual bug analysis — {name}
        </div>
        <button
          type="button"
          data-testid="visual-bug-card-dismiss"
          onClick={onDismiss}
          aria-label="Dismiss"
          style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-dim)", display: "flex" }}
        >
          <X size={14} />
        </button>
      </div>

      {(status === "uploading" || status === "analyzing") && (
        <div data-testid="visual-bug-card-loading" style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-dim)" }}>
          <Loader2 size={14} className="animate-spin" />
          {status === "uploading" ? "Uploading screenshot…" : "Analyzing screenshot (one vision pass)…"}
        </div>
      )}

      {status === "error" && (
        <div data-testid="visual-bug-card-error" style={{ display: "flex", alignItems: "center", gap: 8, color: "#ef4444" }}>
          <AlertTriangle size={14} />
          {error || "Something went wrong analyzing this screenshot."}
        </div>
      )}

      {status === "done" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {result.imageUrl && (
            <img
              data-testid="visual-bug-card-thumbnail"
              src={result.imageUrl}
              alt="Uploaded screenshot"
              style={{ maxWidth: 220, maxHeight: 140, borderRadius: 8, border: "1px solid rgba(255,255,255,0.1)", objectFit: "contain" }}
            />
          )}

          {result.visual_defects?.length > 0 && (
            <div>
              <div style={{ opacity: 0.7, marginBottom: 4 }}>Defects spotted</div>
              <ul data-testid="visual-bug-card-defects" style={{ margin: 0, paddingLeft: 18 }}>
                {result.visual_defects.map((d, i) => <li key={i}>{d}</li>)}
              </ul>
            </div>
          )}

          {result.extracted_strings?.length > 0 && (
            <div>
              <div style={{ opacity: 0.7, marginBottom: 4 }}>Text found in the screenshot</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {result.extracted_strings.map((s, i) => (
                  <span key={i} data-testid="visual-bug-card-extracted-string" style={{
                    padding: "2px 8px", borderRadius: 999, background: "rgba(255,255,255,0.06)",
                    fontFamily: "monospace", fontSize: 12,
                  }}>{s}</span>
                ))}
              </div>
            </div>
          )}

          {result.candidate_files?.length > 0 ? (
            <div>
              <div style={{ opacity: 0.7, marginBottom: 4 }}>Likely source files</div>
              <div data-testid="visual-bug-card-candidates" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {result.candidate_files.map((c, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontFamily: "monospace", fontSize: 12 }}>
                    <FileCode size={12} style={{ flexShrink: 0, opacity: 0.6 }} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{c.path}</span>
                    <span style={{ opacity: 0.5, flexShrink: 0 }}>{Math.round((c.confidence || 0) * 100)}%</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div data-testid="visual-bug-card-no-candidates" style={{ opacity: 0.6 }}>
              No connected repo to search for a matching file yet — analysis above still stands.
            </div>
          )}

          {result.candidate_files?.length > 0 && onGenerateFix && (
            <button
              type="button"
              data-testid="visual-bug-card-generate-fix-btn"
              onClick={onGenerateFix}
              style={{
                alignSelf: "flex-start", display: "flex", alignItems: "center", gap: 6,
                padding: "6px 14px", borderRadius: 999, background: "#FF6608", border: "none",
                color: "#0a0a0a", fontWeight: 600, fontSize: 12, cursor: "pointer",
              }}
            >
              <Wand2 size={13} /> Generate fix
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export default VisualBugAnalysisCard;
