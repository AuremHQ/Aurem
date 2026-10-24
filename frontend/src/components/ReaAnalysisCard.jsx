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
import CodeBlock from "./CodeBlock";

const TYPE_ICON = { js: FileCode2, css: Braces, html: FileText };

function fmtBytes(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function ReaAnalysisCard({ jobId, url }) {
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
        if (!stopped) setPollError(e?.response?.data?.detail || "Lost track of this job.");
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
      <div data-testid="rea-card-error" style={cardStyle}>
        <AlertTriangle size={14} color="#f87171" />
        <span style={{ color: "#f87171", fontSize: 13 }}>{pollError}</span>
      </div>
    );
  }

  const status = job?.status || "running";

  return (
    <div data-testid="rea-analysis-card" style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <Globe size={15} color="#38bdf8" />
        <span style={{ fontWeight: 600, fontSize: 13.5 }}>Reverse-engineering</span>
        <a href={url} target="_blank" rel="noreferrer"
           style={{ color: "#38bdf8", fontSize: 12.5, textDecoration: "none" }}>
          {url}
        </a>
        {status === "running" && (
          <span data-testid="rea-card-running" style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--text-dim)", fontSize: 12 }}>
            <Loader2 size={12} className="animate-spin" /> analyzing…
          </span>
        )}
        {status === "failed" && (
          <span data-testid="rea-card-failed" style={{ color: "#f87171", fontSize: 12 }}>
            failed — {job?.error || "unknown error"}
          </span>
        )}
      </div>

      {status === "done" && (
        <>
          <div style={{ display: "flex", gap: 12, minHeight: 220 }}>
            <div data-testid="rea-file-list" style={{
              width: 260, flexShrink: 0, borderRight: "1px solid rgba(148,163,184,0.18)",
              paddingRight: 10, maxHeight: 340, overflowY: "auto",
            }}>
              {(job.files || []).length === 0 && (
                <div style={{ fontSize: 12, color: "var(--text-dim)" }}>No files captured.</div>
              )}
              {(job.files || []).map((f) => {
                const Icon = TYPE_ICON[f.type] || FileText;
                const isActive = selected?.sha256 === f.sha256;
                return (
                  <button
                    key={f.sha256}
                    data-testid={`rea-file-item-${f.sha256.slice(0, 8)}`}
                    onClick={() => openFile(f)}
                    style={{
                      display: "flex", alignItems: "center", gap: 6, width: "100%",
                      textAlign: "left", padding: "6px 8px", marginBottom: 2,
                      borderRadius: 6, border: "none", cursor: "pointer",
                      background: isActive ? "rgba(56,189,248,0.15)" : "transparent",
                      color: isActive ? "#38bdf8" : "var(--text)",
                      fontSize: 12,
                    }}
                  >
                    <Icon size={13} style={{ flexShrink: 0 }} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                      {f.path}
                    </span>
                    <span style={{ color: "var(--text-dim)", fontSize: 10.5, flexShrink: 0 }}>
                      {fmtBytes(f.bytes)}
                    </span>
                  </button>
                );
              })}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              {!selected && (
                <div style={{ color: "var(--text-dim)", fontSize: 12.5, padding: 10 }}>
                  Click a file on the left to see its real source.
                </div>
              )}
              {selected && fileLoading && (
                <div style={{ color: "var(--text-dim)", fontSize: 12.5, padding: 10 }}>Loading…</div>
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
              <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-dim)" }}>
                {job.observed_api_endpoints.map((ep, i) => (
                  <li key={i}>{typeof ep === "string" ? ep : JSON.stringify(ep)}</li>
                ))}
              </ul>
            </div>
          )}

          {job.backend_note && (
            <div style={{ marginTop: 10, fontSize: 11, color: "var(--text-dim)", fontStyle: "italic" }}>
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
  background: "rgba(15,23,42,0.5)", border: "1px solid rgba(148,163,184,0.18)",
};

export default ReaAnalysisCard;
