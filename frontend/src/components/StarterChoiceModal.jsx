/**
 * StarterChoiceModal.jsx — 2026-09-28 "ACTIVATION FIRST" Part 3.
 *
 * Shown the instant a user skips (or has previously skipped) the
 * repo-connect gate, so nobody lands in an empty chat with no next
 * step. Three starter choices, each a real, working first task:
 *
 *   1. Scan my website     → POST /onboarding/scan-website
 *        Tries Firecrawl first, falls back to our own checks if it's
 *        down/exhausted — the backend always returns a real result.
 *   2. Fix a bug in my code → closes this modal, prefills the chat
 *        composer via the existing `aurem:starter-pick` event (same
 *        mechanism the Advisor panel's prompt picks use) so the user
 *        pastes code into the normal chat, or opens the connect wizard.
 *   3. Try a demo project   → POST /onboarding/demo-project/run
 *        Runs (and verifies) a real fix on a bundled, harmless sample
 *        file. Never touches git, never touches the user's own code.
 *
 * Opened via `window.dispatchEvent(new Event("aurem:open-starter-choices"))`.
 */
import React, { useEffect, useRef, useState } from "react";
import { Globe, Bug, Sparkles, X, Loader2, ArrowRight, CheckCircle2, AlertTriangle, Github } from "lucide-react";
import { api } from "../lib/api";
import useModalA11y from "../hooks/useModalA11y";
import { useOverlaySlot } from "../hooks/useOverlaySlot";
// UI-fix D4 — must render above Z_ADVISOR_TOGGLE (10002); see the
// same note in NewUserWizard.jsx.
import { Z_ACTIVE_MODAL } from "../lib/zIndex";

const SEVERITY_COLOR = { high: "#f87171", medium: "#f59e0b", low: "#64748b" };

function openConnectWizard() {
  window.dispatchEvent(new Event("aurem:open-connect-wizard"));
}

function Panel({ children, onClose, testId }) {
  const ref = useRef(null);
  useModalA11y({ ref, isOpen: true, onClose });
  return (
    <div
      data-testid={testId || "starter-choice-modal"}
      ref={ref}
      role="dialog" aria-modal="true" aria-labelledby="starter-choice-title"
      tabIndex={-1}
      style={{
        position: "fixed", inset: 0, zIndex: Z_ACTIVE_MODAL,
        background: "rgba(8,10,14,0.72)",
        backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 16,
      }}
    >
      <div style={{
        width: "min(560px, 100%)", maxHeight: "92vh", overflowY: "auto",
        background: "#0f172a", border: "0.5px solid rgba(255,255,255,0.1)",
        borderRadius: 14, boxShadow: "0 24px 60px -16px rgba(245,158,11,0.18)",
        display: "flex", flexDirection: "column",
      }}>
        {children}
      </div>
    </div>
  );
}

function Header({ title, onClose }) {
  return (
    <header style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "14px 20px", borderBottom: "0.5px solid rgba(255,255,255,0.08)",
      position: "sticky", top: 0, background: "#0f172a", zIndex: 1,
    }}>
      <div id="starter-choice-title" style={{ fontSize: 14, fontWeight: 600, color: "#f8fafc" }}>
        {title}
      </div>
      <button data-testid="starter-choice-close" onClick={onClose} title="Close"
              style={{ background: "transparent", border: "none", padding: 4,
                       color: "#64748b", cursor: "pointer", display: "flex" }}>
        <X size={16} />
      </button>
    </header>
  );
}

function ChoiceButton({ testId, icon, label, sub, onClick }) {
  return (
    <button
      data-testid={testId}
      onClick={onClick}
      style={{
        display: "flex", alignItems: "center", gap: 14, width: "100%",
        padding: "16px 18px", background: "rgba(255,255,255,0.03)",
        border: "0.5px solid rgba(255,255,255,0.1)", borderRadius: 12,
        cursor: "pointer", textAlign: "left", transition: "background 0.15s, border-color 0.15s",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(245,158,11,0.08)"; e.currentTarget.style.borderColor = "rgba(245,158,11,0.4)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(255,255,255,0.03)"; e.currentTarget.style.borderColor = "rgba(255,255,255,0.1)"; }}
    >
      <div style={{ width: 38, height: 38, borderRadius: 10, background: "rgba(245,158,11,0.12)",
                     display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        {icon}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: "#f8fafc" }}>{label}</div>
        <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>{sub}</div>
      </div>
      <ArrowRight size={16} color="#64748b" />
    </button>
  );
}

function PrimaryButton({ testId, children, onClick, disabled }) {
  return (
    <button data-testid={testId} onClick={onClick} disabled={disabled}
      style={{
        display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
        padding: "11px 18px", background: disabled ? "rgba(245,158,11,0.4)" : "#f59e0b",
        color: "#000", border: "none", borderRadius: 10, fontSize: 13, fontWeight: 700,
        cursor: disabled ? "default" : "pointer", width: "100%",
      }}>
      {children}
    </button>
  );
}

function SecondaryButton({ testId, children, onClick }) {
  return (
    <button data-testid={testId} onClick={onClick}
      style={{
        display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
        padding: "10px 16px", background: "transparent", color: "#94a3b8",
        border: "0.5px solid rgba(255,255,255,0.12)", borderRadius: 10, fontSize: 13,
        fontWeight: 500, cursor: "pointer", width: "100%",
      }}>
      {children}
    </button>
  );
}

export default function StarterChoiceModal() {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState("choices"); // choices | scan_input | scan_result | demo_running | demo_result
  const [scanUrl, setScanUrl] = useState("");
  const [scanBusy, setScanBusy] = useState(false);
  const [scanError, setScanError] = useState("");
  const [scanResult, setScanResult] = useState(null);
  const [demoResult, setDemoResult] = useState(null);
  const [demoError, setDemoError] = useState("");
  // UI-fix D1 — announce this slot to the overlay coordinator so the
  // mascot's auto-open bubble doesn't pile on top of it.
  useOverlaySlot("starter", open);

  useEffect(() => {
    const onOpen = () => { setOpen(true); setStep("choices"); };
    window.addEventListener("aurem:open-starter-choices", onOpen);
    return () => window.removeEventListener("aurem:open-starter-choices", onOpen);
  }, []);

  const close = () => setOpen(false);

  const pickPasteCode = () => {
    close();
    window.dispatchEvent(new CustomEvent("aurem:starter-pick", {
      detail: { prompt: "Here's a bug in my code — please find and fix it:\n\n" },
    }));
  };

  const pickScan = () => { setStep("scan_input"); setScanError(""); };

  const runScan = async () => {
    if (!scanUrl.trim()) return;
    setScanBusy(true); setScanError("");
    try {
      const { data } = await api.post("/onboarding/scan-website", { url: scanUrl.trim() });
      setScanResult(data);
      setStep("scan_result");
    } catch (e) {
      setScanError(e?.response?.data?.detail || "Couldn't scan that site — try another URL.");
    } finally {
      setScanBusy(false);
    }
  };

  const pickDemo = async () => {
    setStep("demo_running"); setDemoError("");
    try {
      const { data } = await api.post("/onboarding/demo-project/run");
      setDemoResult(data);
      setStep("demo_result");
    } catch (e) {
      setDemoError(e?.response?.data?.detail || "The demo hit a snag — please try again.");
      setStep("demo_result");
    }
  };

  if (!open) return null;

  if (step === "choices") {
    return (
      <Panel onClose={close}>
        <Header title="Let's get your first win" onClose={close} />
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ fontSize: 12.5, color: "#94a3b8", marginBottom: 4 }}>
            No repo connected yet — no problem. Pick one and ORA will do something real right now.
          </div>
          <ChoiceButton testId="starter-choice-scan" onClick={pickScan}
            icon={<Globe size={18} color="#f59e0b" />}
            label="Scan my website" sub="Paste a URL, get a real list of issues in seconds" />
          <ChoiceButton testId="starter-choice-bugfix" onClick={pickPasteCode}
            icon={<Bug size={18} color="#f59e0b" />}
            label="Fix a bug in my code" sub="Paste a snippet and describe what's wrong" />
          <ChoiceButton testId="starter-choice-demo" onClick={pickDemo}
            icon={<Sparkles size={18} color="#f59e0b" />}
            label="Try a demo project" sub="Watch ORA find and verify-fix a real bug, right now" />
          <div style={{ marginTop: 8 }}>
            <SecondaryButton testId="starter-choice-connect-instead" onClick={() => { close(); openConnectWizard(); }}>
              <Github size={14} /> Connect GitHub instead
            </SecondaryButton>
          </div>
        </div>
      </Panel>
    );
  }

  if (step === "scan_input") {
    return (
      <Panel onClose={close}>
        <Header title="Scan my website" onClose={close} />
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ fontSize: 12.5, color: "#94a3b8" }}>
            Any public URL — we'll check load speed, SEO basics, and broken links.
          </div>
          <input
            data-testid="starter-scan-url-input"
            value={scanUrl}
            onChange={(e) => setScanUrl(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") runScan(); }}
            placeholder="example.com"
            autoFocus
            style={{
              padding: "11px 14px", background: "rgba(255,255,255,0.04)",
              border: "0.5px solid rgba(255,255,255,0.15)", borderRadius: 10,
              color: "#f8fafc", fontSize: 13, outline: "none",
            }}
          />
          {scanError && (
            <div data-testid="starter-scan-error" style={{ fontSize: 12, color: "#f87171" }}>{scanError}</div>
          )}
          <PrimaryButton testId="starter-scan-submit" onClick={runScan} disabled={scanBusy || !scanUrl.trim()}>
            {scanBusy ? <><Loader2 size={14} className="animate-spin" /> Scanning…</> : "Scan it"}
          </PrimaryButton>
        </div>
      </Panel>
    );
  }

  if (step === "scan_result" && scanResult) {
    return (
      <Panel onClose={close}>
        <Header title="Scan results" onClose={close} />
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
          <div data-testid="starter-scan-summary" style={{ fontSize: 13.5, color: "#f8fafc", fontWeight: 600 }}>
            {scanResult.summary}
          </div>
          {scanResult.scan_path === "fallback" && (
            <div style={{ fontSize: 11, color: "#64748b" }}>(ran our backup checker — full scanner was busy)</div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {scanResult.issues.map((iss, i) => (
              <div key={i} data-testid={`starter-scan-issue-${i}`} style={{
                display: "flex", gap: 10, padding: "10px 12px",
                background: "rgba(255,255,255,0.03)", borderRadius: 8,
                borderLeft: `3px solid ${SEVERITY_COLOR[iss.severity] || "#64748b"}`,
              }}>
                <AlertTriangle size={14} color={SEVERITY_COLOR[iss.severity]} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  <div style={{ fontSize: 12.5, color: "#f8fafc", fontWeight: 600 }}>{iss.title}</div>
                  <div style={{ fontSize: 11.5, color: "#94a3b8", marginTop: 2 }}>{iss.detail}</div>
                </div>
              </div>
            ))}
            {scanResult.issues.length === 0 && (
              <div style={{ fontSize: 12.5, color: "#4ade80" }}>Nothing obvious found — nice work!</div>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <SecondaryButton testId="starter-scan-again" onClick={() => setStep("scan_input")}>
              Scan another site
            </SecondaryButton>
          </div>
          <PrimaryButton testId="starter-scan-connect-cta" onClick={() => { close(); openConnectWizard(); }}>
            <Github size={14} /> Connect GitHub so ORA can fix these
          </PrimaryButton>
        </div>
      </Panel>
    );
  }

  if (step === "demo_running") {
    return (
      <Panel onClose={close}>
        <Header title="Try a demo project" onClose={close} />
        <div data-testid="starter-demo-running" style={{ padding: 40, display: "flex",
              flexDirection: "column", alignItems: "center", gap: 14 }}>
          <Loader2 size={28} className="animate-spin" color="#f59e0b" />
          <div style={{ fontSize: 13, color: "#94a3b8", textAlign: "center" }}>
            ORA is finding and fixing a real bug in a small sample project…
          </div>
        </div>
      </Panel>
    );
  }

  if (step === "demo_result") {
    return (
      <Panel onClose={close}>
        <Header title="Try a demo project" onClose={close} />
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
          {demoError && (
            <div data-testid="starter-demo-error" style={{ fontSize: 12.5, color: "#f87171" }}>{demoError}</div>
          )}
          {demoResult && (
            <>
              <div data-testid="starter-demo-verified-badge" style={{
                display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 600,
                color: demoResult.verified ? "#4ade80" : "#f59e0b",
              }}>
                {demoResult.verified ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
                {demoResult.summary}
              </div>
              <div style={{ fontSize: 12.5, color: "#cbd5e1" }}>{demoResult.explanation}</div>
              <pre data-testid="starter-demo-diff" style={{
                background: "#050810", border: "0.5px solid rgba(255,255,255,0.08)",
                borderRadius: 8, padding: 12, fontSize: 11.5, lineHeight: 1.6,
                overflowX: "auto", color: "#94a3b8", fontFamily: "ui-monospace, monospace",
              }}>
                {demoResult.diff.split("\n").map((line, i) => (
                  <div key={i} style={{
                    color: line.startsWith("+") ? "#4ade80" : line.startsWith("-") ? "#f87171" : "#64748b",
                  }}>{line}</div>
                ))}
              </pre>
            </>
          )}
          <PrimaryButton testId="starter-demo-connect-cta" onClick={() => { close(); openConnectWizard(); }}>
            <Github size={14} /> Connect GitHub to fix bugs like this in YOUR code
          </PrimaryButton>
          <SecondaryButton testId="starter-demo-back" onClick={() => setStep("choices")}>
            Back to starter choices
          </SecondaryButton>
        </div>
      </Panel>
    );
  }

  return null;
}
