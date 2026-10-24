/**
 * components/ReaAnalysisCard.jsx — admin-only "reverse engineer this
 * website" result card (github.com/morluto/rea). Rendered inline in
 * chat by MessageBubble when a message has `kind: "rea_job"`.
 *
 * Two-pane layout: file list on the left (path, type, size — click
 * to load), real source text on the right (CodeBlock, read-only,
 * same Monaco viewer used elsewhere in the app). Backend and frontend
 * scope note is always shown — proprietary server source is never
 * recoverable from a live site, only what the browser actually
 * received (HTML/CSS/JS, de-minified where a source map exists) plus
 * OBSERVED API calls (not their server-side implementation).
 */
import React, { useEffect, useState } from "react";
import { FileCode2, FileText, Braces, AlertTriangle, Loader2, Globe } from "lucide-react";
import { api } from "../lib/api";
import { sanitizeErrorMessage } from "../lib/sanitizeError";
import CodeBlock from "./CodeBlock";

const TYPE_ICON = { js: FileCode2, css: Braces, html: FileText };

// Light palette — matches pages/OraDirect.jsx's PAL (cream/Claude-style
// theme). The dark theme below keeps the original app-wide chat colors
// unchanged (ChatPanel.jsx's usage). CodeBlock itself always stays
// dark ("vs-dark" Monaco) in both — same convention as every other
// code block in the app, light page or not.
const THEMES = {
  dark: {
    cardBg: "rgba(15,23,42,0.5)", cardBorder: "1px solid rgba(148,163,184,0.18)",
    divider: "1px solid rgba(148,163,184,0.18)",
    text: "var(--text)", textDim: "var(--text-dim)",
    accent: "#38bdf8", accentBg: "rgba(56,189,248,0.15)",
    danger: "#f87171",
  },
  light: {
    cardBg: "#FFFFFF", cardBorder: "1px solid #E5E5DF",
    divider: "1px solid #E5E5DF",
    text: "#1C1C19", textDim: "#6B6B63",
    accent: "#D56A4F", accentBg: "rgba(213,106,79,0.10)",
    danger: "#C94A37",
  },
};

function fmtBytes(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function ReaAnalysisCard({ jobId, url, theme = "dark" }) {
  const t = THEMES[theme] || THEMES.dark;
  const [job, setJob] = useState(null);
  const [pollError, setPollError] = useState(null);
  const [selected, setSelected] = useState(null); // { sha256, text, path, type }
  const [fileLoading, setFileLoading] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    let timer = null;

    async function poll() {
      try {
        const r = await api.get(`/admin/rea/jobs/${jobId}`);
        if (stopped) return;
        setJob(r.data);
        if (r.data?.status === "running") {
          timer = setTimeout(poll, 3000);
        }
      } catch (e) {
        if (!stopped) setPollError(sanitizeErrorMessage(e, "Lost track of this job."));
      }
    }
    poll();
    return () => { stopped = true; if (timer) clearTimeout(timer); };
  }, [jobId]);

  async function openFile(f) {
    setSelected({ ...f, text: null });
    setFileLoading(true);
    try {
      const r = await api.get(`/admin/rea/jobs/${jobId}/file/${f.sha256}`);
      setSelected({ ...f, text: r.data.text });
    } catch {
      setSelected({ ...f, text: "// Could not load this file's source." });
    } finally {
      setFileLoading(false);
    }
  }

  if (pollError) {
    return (
      <div data-testid="rea-card-error" style={{ ...cardStyle, background: t.cardBg, border: t.cardBorder }}>
        <AlertTriangle size={14} color={t.danger} />
        <span style={{ color: t.danger, fontSize: 13 }}>{pollError}</span>
      </div>
    );
  }

  const status = job?.status || "running";

  return (
    <div data-testid="rea-analysis-card" style={{ ...cardStyle, background: t.cardBg, border: t.cardBorder, color: t.text }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <Globe size={15} color={t.accent} />
        <span style={{ fontWeight: 600, fontSize: 13.5 }}>Reverse-engineering</span>
        <a href={url} target="_blank" rel="noreferrer"
           style={{ color: t.accent, fontSize: 12.5, textDecoration: "none" }}>
          {url}
        </a>
        {status === "running" && (
          <span data-testid="rea-card-running" style={{ display: "inline-flex", alignItems: "center", gap: 4, color: t.textDim, fontSize: 12 }}>
            <Loader2 size={12} className="animate-spin" /> analyzing…
          </span>
        )}
        {status === "failed" && (
          <span data-testid="rea-card-failed" style={{ color: t.danger, fontSize: 12 }}>
            failed — {job?.error || "unknown error"}
          </span>
        )}
      </div>

      {status === "done" && (
        <>
          <div style={{ display: "flex", gap: 12, minHeight: 220 }}>
            <div data-testid="rea-file-list" style={{
              width: 260, flexShrink: 0, borderRight: t.divider,
              paddingRight: 10, maxHeight: 340, overflowY: "auto",
            }}>
              {(job.files || []).length === 0 && (
                <div style={{ fontSize: 12, color: t.textDim }}>No files captured.</div>
              )}
              {(job.files || []).map((f, i) => {
                const Icon = TYPE_ICON[f.type] || FileText;
                const isActive = selected?.sha256 === f.sha256;
                return (
                  <button
                    key={`${f.sha256}-${i}`}
                    data-testid={`rea-file-item-${f.sha256.slice(0, 8)}-${i}`}
                    onClick={() => openFile(f)}
                    style={{
                      display: "flex", alignItems: "center", gap: 6, width: "100%",
                      textAlign: "left", padding: "6px 8px", marginBottom: 2,
                      borderRadius: 6, border: "none", cursor: "pointer",
                      background: isActive ? t.accentBg : "transparent",
                      color: isActive ? t.accent : t.text,
                      fontSize: 12,
                    }}
                  >
                    <Icon size={13} style={{ flexShrink: 0 }} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                      {f.path}
                    </span>
                    <span style={{ color: t.textDim, fontSize: 10.5, flexShrink: 0 }}>
                      {fmtBytes(f.bytes)}
                    </span>
                  </button>
                );
              })}
            </div>
            <div style={{ flex: 1, minWidth: 0, minHeight: 320 }}>
              {!selected && (
                <div data-testid="rea-code-placeholder" style={{ color: t.textDim, fontSize: 12.5, padding: 10 }}>
                  Click a file on the left to see its real source.
                </div>
              )}
              {selected && fileLoading && (
                <div style={{ color: t.textDim, fontSize: 12.5, padding: 10 }}>Loading…</div>
              )}
              {selected && !fileLoading && (
                <CodeBlock
                  code={selected.text || ""}
                  language={selected.type === "js" ? "javascript" : selected.type}
                  filename={selected.path}
                />
              )}
            </div>
          </div>

          {job.observed_api_endpoints?.length > 0 && (
            <div data-testid="rea-observed-endpoints" style={{ marginTop: 12, fontSize: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Observed API calls (not source code)</div>
              <ul style={{ margin: 0, paddingLeft: 18, color: t.textDim }}>
                {job.observed_api_endpoints.map((ep, i) => (
                  <li key={i}>{typeof ep === "string" ? ep : JSON.stringify(ep)}</li>
                ))}
              </ul>
            </div>
          )}

          {job.backend_note && (
            <div data-testid="rea-backend-note" style={{ marginTop: 10, fontSize: 11, color: t.textDim, fontStyle: "italic" }}>
              {job.backend_note}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const cardStyle = {
  marginTop: 10, padding: 14, borderRadius: 10,
};

export default ReaAnalysisCard;
