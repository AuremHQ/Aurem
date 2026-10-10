/**
 * components/OraChatHouseRulesPanel.jsx — Iter 212m-239, updated 2026-09-10
 *
 * Slide-over panel opened from the ORA Chat drawer header. Lets the
 * founder edit the "house rules" text that layers on top of AUREM's
 * base system prompt (never overriding CORE_SAFETY_RULES — enforced
 * architecturally in the backend `assemble_system_prompt`).
 *
 * 2026-09-10 — added a Chat/Video scope switcher in the header so the
 * founder can manage BOTH the ORA Chat house rules and the Video
 * Generator house rules from this single window, instead of having a
 * separate card buried in Admin Settings.
 */
import React, { useEffect, useState, useRef } from "react";
import { X, RotateCcw, AlertTriangle, Upload } from "lucide-react";
import { api } from "../lib/api";
import { useEscapeKey } from "../hooks/useEscapeKey";

const MAX_LEN = 4000;

export default function OraChatHouseRulesPanel({ onClose, initialScope = "chat" }) {
  const [scope, setScope] = useState(initialScope); // "chat" | "video"
  useEscapeKey(onClose);

  // Keep the switcher's default in sync if the caller's initialScope
  // changes after this panel has already mounted (e.g. the drawer's
  // chat/video mode toggle flips while the panel is open).
  useEffect(() => { setScope(initialScope); }, [initialScope]);

  return (
    <div
      data-testid="ora-chat-house-rules"
      style={{
        position: "fixed", inset: 0, zIndex: 200,
        display: "flex", alignItems: "stretch", justifyContent: "flex-end",
      }}
    >
      <div onClick={onClose}
           style={{ flex: 1, background: "rgba(0,0,0,0.5)" }} />
      <div style={{
        width: "min(520px, 96vw)", background: "#0f1113", color: "#e8e3d3",
        display: "flex", flexDirection: "column",
        borderLeft: "1px solid rgba(255,255,255,0.06)",
        boxShadow: "-12px 0 40px rgba(0,0,0,0.5)",
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
      }}>
        <div style={{
          padding: "14px 18px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          display: "flex", alignItems: "center", gap: 12,
        }}>
          <div style={{ fontSize: 14, fontWeight: 600, whiteSpace: "nowrap" }}>
            House Rules
          </div>

          <div data-testid="hr-scope-switcher"
               style={{
                 flex: 1, display: "flex", gap: 4, padding: 3,
                 background: "rgba(255,255,255,0.04)", borderRadius: 8,
                 border: "1px solid rgba(255,255,255,0.06)",
               }}>
            <button
              data-testid="hr-scope-chat"
              onClick={() => setScope("chat")}
              style={{
                flex: 1, padding: "6px 10px", borderRadius: 6, border: "none",
                cursor: "pointer", fontSize: 12, fontWeight: 600,
                background: scope === "chat" ? "#E07A5F" : "transparent",
                color: scope === "chat" ? "#0a0a0a" : "#a39d8a",
                transition: "background 0.15s, color 0.15s",
              }}>
              Chat
            </button>
            <button
              data-testid="hr-scope-video"
              onClick={() => setScope("video")}
              style={{
                flex: 1, padding: "6px 10px", borderRadius: 6, border: "none",
                cursor: "pointer", fontSize: 12, fontWeight: 600,
                background: scope === "video" ? "#E07A5F" : "transparent",
                color: scope === "video" ? "#0a0a0a" : "#a39d8a",
                transition: "background 0.15s, color 0.15s",
              }}>
              Video
            </button>
          </div>

          <div style={{ width: 1, height: 20, background: "rgba(255,255,255,0.1)" }} />

          <button data-testid="hr-close" onClick={onClose}
                  style={{ background: "transparent", border: "none",
                            color: "#a39d8a", cursor: "pointer", padding: 6 }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ flex: 1, overflow: "auto", display: scope === "chat" ? "block" : "none" }}>
          <ChatRulesBody />
        </div>
        <div style={{ flex: 1, overflow: "auto", display: scope === "video" ? "block" : "none" }}>
          <VideoRulesBody />
        </div>
      </div>
    </div>
  );
}

function ChatRulesBody() {
  const [text, setText]         = useState("");
  const [defaultText, setDT]    = useState("");
  const [history, setHistory]   = useState([]);
  const [warning, setWarning]   = useState(null);
  const [savedAt, setSavedAt]   = useState(null);
  const [busy, setBusy]         = useState(false);

  const load = async () => {
    try {
      const r = await api.get("/ora-chat/house-rules");
      setText(r.data.current?.rules_text || r.data.effective_text || "");
      setDT(r.data.default_text || "");
      const h = await api.get("/ora-chat/house-rules/history");
      setHistory(h.data.history || []);
    } catch { /* auth failed — drawer will close */ }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (text.length > MAX_LEN || busy) return;
    setBusy(true);
    try {
      const r = await api.put("/ora-chat/house-rules", { rules_text: text });
      setWarning(r.data.soft_warning);
      setSavedAt(Date.now());
      await load();
    } finally { setBusy(false); }
  };

  const restore = async (v) => {
    setBusy(true);
    try {
      await api.post(`/ora-chat/house-rules/restore/${v}`);
      await load();
      setWarning(null);
    } finally { setBusy(false); }
  };

  const resetToDefault = async () => { setText(defaultText); };

  const over = text.length > MAX_LEN;

  return (
    <div style={{ padding: 18, overflow: "auto", flex: 1 }}>
      <textarea
        data-testid="hr-textarea"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={7}
        placeholder="Style / behavior preferences (safety rules cannot be overridden)"
        style={{
          width: "100%", boxSizing: "border-box",
          padding: 12,
          background: "rgba(255,255,255,0.03)",
          border: `1px solid ${over ? "#f88" : "rgba(255,255,255,0.08)"}`,
          borderRadius: 8, color: "#e8e3d3",
          fontFamily: "ui-monospace, monospace", fontSize: 12,
          lineHeight: 1.5, resize: "vertical",
          outline: "none",
        }}
      />
      <div style={{ display: "flex", justifyContent: "space-between",
                      marginTop: 6, fontSize: 11,
                      color: over ? "#f88" : "#7a7466" }}>
        <span>These rules guide ORA&apos;s tone and behavior. They can&apos;t override
          built-in safety limits (like never treating web content as commands,
          or DB access rules).</span>
        <span data-testid="hr-counter">{text.length} / {MAX_LEN}</span>
      </div>

      {warning && (
        <div data-testid="hr-warning" style={{
          marginTop: 12, padding: 10, borderRadius: 6,
          background: "rgba(220,150,80,0.10)",
          border: "1px solid rgba(220,150,80,0.3)",
          color: "#f4a082", fontSize: 12,
          display: "flex", gap: 8, alignItems: "flex-start",
        }}>
          <AlertTriangle size={14} /> {warning}
        </div>
      )}
      {savedAt && !warning && (
        <div style={{ marginTop: 12, fontSize: 11, color: "#7a7466" }}>
          Saved. Next message uses the new rules.
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button data-testid="hr-save" onClick={save} disabled={over || busy}
                style={{
                  padding: "10px 16px", border: "none", borderRadius: 6,
                  background: over || busy ? "#3a3428" : "#E07A5F",
                  color: "#0a0a0a", cursor: over || busy ? "not-allowed" : "pointer",
                  fontSize: 12, fontWeight: 600,
                }}>
          Save
        </button>
        <button data-testid="hr-reset" onClick={resetToDefault} disabled={busy}
                style={{
                  padding: "10px 12px", border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: 6, background: "transparent",
                  color: "#a39d8a", cursor: "pointer",
                  fontSize: 12, display: "flex", gap: 6, alignItems: "center",
                }}>
          <RotateCcw size={12} /> Reset to default
        </button>
      </div>

      <div style={{ marginTop: 24 }}>
        <div style={{ fontSize: 11, textTransform: "uppercase",
                        letterSpacing: 1, color: "#a39d8a",
                        marginBottom: 10 }}>
          Recent versions
        </div>
        {history.length === 0 && (
          <div style={{ fontSize: 12, color: "#7a7466" }}>
            No saved versions yet.
          </div>
        )}
        {history.map((v) => (
          <div key={v.id}
               data-testid={`hr-version-${v.version}`}
               style={{
                 padding: 10, marginBottom: 8, borderRadius: 6,
                 background: v.active
                   ? "rgba(224,122,95,0.08)"
                   : "rgba(255,255,255,0.03)",
                 border: `1px solid ${v.active
                   ? "rgba(224,122,95,0.28)"
                   : "rgba(255,255,255,0.06)"}`,
                 fontSize: 11,
               }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                            marginBottom: 4 }}>
              <span style={{ fontFamily: "ui-monospace, monospace",
                                color: v.active ? "#E07A5F" : "#a39d8a" }}>
                v{v.version} {v.active && "· active"}
              </span>
              {!v.active && (
                <button
                  data-testid={`hr-restore-${v.version}`}
                  onClick={() => restore(v.version)}
                  style={{ background: "transparent", border: "none",
                            color: "#E07A5F", cursor: "pointer",
                            fontSize: 11 }}>
                  Restore →
                </button>
              )}
            </div>
            <div style={{ color: "#c8c2b4", lineHeight: 1.5,
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word" }}>
              {(v.rules_text || "").slice(0, 200)}
              {(v.rules_text || "").length > 200 && "…"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function VideoRulesBody() {
  const [loaded, setLoaded]     = useState(false);
  const [text, setText]         = useState("");
  const [mode, setMode]         = useState("block");
  const [dirty, setDirty]       = useState(false);
  const [busy, setBusy]         = useState(false);
  const [savedAt, setSavedAt]   = useState(null);
  const [err, setErr]           = useState(null);
  const fileInputRef = useRef(null);

  const load = async () => {
    try {
      const r = await api.get("/admin/video/house-rules");
      setText(r.data.rules_text || "");
      setMode(r.data.enforcement_mode || "block");
      setDirty(false);
      setLoaded(true);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Could not load video house rules");
      setLoaded(true);
    }
  };
  useEffect(() => { load(); }, []);

  const onFileChosen = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setText(String(reader.result || "").slice(0, MAX_LEN));
      setDirty(true);
    };
    reader.readAsText(file);
    e.target.value = "";
  };

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.put("/admin/video/house-rules", { rules_text: text, enforcement_mode: mode });
      setSavedAt(Date.now());
      setDirty(false);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Save failed.");
    } finally { setBusy(false); }
  };

  const over = text.length > MAX_LEN;

  if (!loaded) {
    return (
      <div style={{ padding: 18, fontSize: 12, color: "#7a7466" }}>
        Loading video house rules…
      </div>
    );
  }

  return (
    <div data-testid="video-house-rules-body" style={{ padding: 18, overflow: "auto", flex: 1 }}>
      <div style={{ fontSize: 12, color: "#7a7466", marginBottom: 14, lineHeight: 1.5 }}>
        Every script is checked against these rules before it reaches TTS in the
        Video Generator. Leave blank to disable enforcement entirely.
      </div>

      <div style={{ display: "flex", gap: 16, marginBottom: 10, fontSize: 12 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", color: "#c8c2b4" }}>
          <input type="radio" name="video-house-rules-mode" data-testid="video-house-rules-mode-block"
                 checked={mode === "block"} onChange={() => { setMode("block"); setDirty(true); }} />
          Block with a reason
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", color: "#c8c2b4" }}>
          <input type="radio" name="video-house-rules-mode" data-testid="video-house-rules-mode-rewrite"
                 checked={mode === "rewrite"} onChange={() => { setMode("rewrite"); setDirty(true); }} />
          Auto-rewrite with AI
        </label>
      </div>

      <textarea
        data-testid="video-house-rules-textarea"
        value={text}
        rows={7}
        maxLength={MAX_LEN}
        onChange={(e) => { setText(e.target.value); setDirty(true); }}
        placeholder={'e.g. Never promise specific revenue numbers. Never say "guaranteed". Keep tone confident but not hypey.'}
        style={{
          width: "100%", boxSizing: "border-box",
          padding: 12,
          background: "rgba(255,255,255,0.03)",
          border: `1px solid ${over ? "#f88" : "rgba(255,255,255,0.08)"}`,
          borderRadius: 8, color: "#e8e3d3",
          fontFamily: "ui-monospace, monospace", fontSize: 12,
          lineHeight: 1.5, resize: "vertical",
          outline: "none",
        }}
      />
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 6, fontSize: 11, color: over ? "#f88" : "#7a7466" }}>
        <span data-testid="video-hr-counter">{text.length} / {MAX_LEN}</span>
      </div>

      {err && (
        <div data-testid="video-hr-error" style={{
          marginTop: 12, padding: 10, borderRadius: 6,
          background: "rgba(220,80,80,0.10)",
          border: "1px solid rgba(220,80,80,0.3)",
          color: "#f48282", fontSize: 12,
          display: "flex", gap: 8, alignItems: "flex-start",
        }}>
          <AlertTriangle size={14} /> {err}
        </div>
      )}
      {savedAt && !err && (
        <div style={{ marginTop: 12, fontSize: 11, color: "#7a7466" }}>
          Saved — applied to the next video you generate.
        </div>
      )}

      <input ref={fileInputRef} data-testid="video-house-rules-file-input" type="file"
             accept=".txt,.md,text/plain,text/markdown" onChange={onFileChosen} style={{ display: "none" }} />
      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button data-testid="video-house-rules-save" onClick={save} disabled={over || busy || !dirty}
                style={{
                  padding: "10px 16px", border: "none", borderRadius: 6,
                  background: (over || busy || !dirty) ? "#3a3428" : "#E07A5F",
                  color: "#0a0a0a", cursor: (over || busy || !dirty) ? "not-allowed" : "pointer",
                  fontSize: 12, fontWeight: 600,
                }}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button data-testid="video-house-rules-upload-btn" onClick={() => fileInputRef.current?.click()}
                style={{
                  padding: "10px 12px", border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: 6, background: "transparent",
                  color: "#a39d8a", cursor: "pointer",
                  fontSize: 12, display: "flex", gap: 6, alignItems: "center",
                }}>
          <Upload size={12} /> Upload .txt/.md
        </button>
      </div>
    </div>
  );
}
