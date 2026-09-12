/**
 * CodeDiffViewer.jsx — 2026-09-12
 * Collapsible unified-diff renderer. Diffs are computed server-side
 * (Python difflib) — this just colors +/- lines, no diff library.
 */
import { useState } from "react";
import { ChevronDown, ChevronRight, FileCode, AlertTriangle } from "lucide-react";

const DiffLine = ({ line }) => {
  let color = "var(--text-dim)";
  let bg = "transparent";
  if (line.startsWith("+") && !line.startsWith("+++")) { color = "#4ade80"; bg = "rgba(74,222,128,0.08)"; }
  else if (line.startsWith("-") && !line.startsWith("---")) { color = "#f87171"; bg = "rgba(248,113,113,0.08)"; }
  else if (line.startsWith("@@")) { color = "#60a5fa"; bg = "rgba(96,165,250,0.08)"; }
  return (
    <div style={{ color, background: bg, fontFamily: "monospace", fontSize: 12,
                  padding: "1px 8px", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
      {line || " "}
    </div>
  );
};

const FileDiffBlock = ({ file }) => {
  const [open, setOpen] = useState(true);
  const lines = (file.diff || "").split("\n").filter((_, i, arr) => !(i === arr.length - 1 && arr[i] === ""));

  return (
    <div data-testid="code-diff-file-block" style={{ borderRadius: 8, border: "1px solid rgba(255,255,255,0.1)", overflow: "hidden" }}>
      <button
        type="button"
        data-testid="code-diff-file-toggle"
        onClick={() => setOpen((o) => !o)}
        style={{ width: "100%", display: "flex", alignItems: "center", gap: 6, padding: "8px 10px",
                 background: "rgba(255,255,255,0.04)", border: "none", cursor: "pointer",
                 color: "var(--text-primary)", fontSize: 12, fontFamily: "monospace" }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <FileCode size={13} style={{ opacity: 0.6 }} />
        {file.path}
        {file.has_syntax_error && (
          <span data-testid="code-diff-syntax-error-badge" style={{ marginLeft: "auto", display: "flex", alignItems: "center",
                        gap: 4, color: "#f87171", fontSize: 11 }}>
            <AlertTriangle size={12} /> syntax error
          </span>
        )}
      </button>
      {open && (
        <div style={{ maxHeight: 320, overflowY: "auto", padding: "6px 0" }}>
          {lines.map((l, i) => <DiffLine key={i} line={l} />)}
        </div>
      )}
      {open && file.has_syntax_error && (
        <div data-testid="code-diff-syntax-error-detail" style={{ padding: 8, fontSize: 11, color: "#f87171",
                      fontFamily: "monospace", borderTop: "1px solid rgba(248,113,113,0.2)" }}>
          {file.syntax_error}
        </div>
      )}
    </div>
  );
};

export const CodeDiffViewer = ({ files }) => {
  if (!files?.length) return null;
  return (
    <div data-testid="code-diff-viewer" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {files.map((f, i) => <FileDiffBlock key={i} file={f} />)}
    </div>
  );
};

export default CodeDiffViewer;
