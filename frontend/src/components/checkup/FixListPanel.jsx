import React, { useRef } from "react";
import { X } from "lucide-react";
import useModalA11y from "../../hooks/useModalA11y";
import { Z_ACTIVE_MODAL } from "../../lib/zIndex";
import { STAGE_LABELS, STAGE_ORDER, SEVERITY_LABEL, itemPrompt } from "./copy";

export default function FixListPanel({ items, onClose, onIgnore }) {
  const ref = useRef(null);
  useModalA11y({ ref, isOpen: true, onClose });
  const askOra = (item) => {
    window.dispatchEvent(new CustomEvent("aurem:starter-pick", { detail: { prompt: itemPrompt(item) } }));
    onClose();
  };
  return (
    <aside data-testid="fix-list-panel" ref={ref} role="dialog" aria-modal="true" aria-labelledby="fix-list-title" tabIndex={-1}
      style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: "min(420px, 100vw)", zIndex: Z_ACTIVE_MODAL,
               background: "var(--bg-elevated, #12151c)", color: "var(--text, #e6e8ee)", padding: 20, overflowY: "auto",
               boxShadow: "-8px 0 32px rgba(0,0,0,0.4)" }}>
      <button type="button" aria-label="Close fix list" data-testid="fix-list-close" onClick={onClose}
        style={{ position: "absolute", top: 12, right: 12, background: "none", border: "none", color: "inherit", cursor: "pointer" }}>
        <X size={18} aria-hidden="true" />
      </button>
      <h2 id="fix-list-title" style={{ marginTop: 0 }}>Things to fix</h2>
      {items.length === 0 && <p data-testid="fix-list-empty">Nothing to fix right now.</p>}
      {STAGE_ORDER.map((stage) => {
        const mine = items.filter((i) => i.stage === stage);
        if (!mine.length) return null;
        return (
          <section key={stage} aria-label={STAGE_LABELS[stage]} style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: 14, opacity: 0.8 }}>{STAGE_LABELS[stage]}</h3>
            {mine.map((i) => (
              <div key={i.id} data-testid="fix-list-item" style={{ padding: 10, border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8, marginBottom: 8 }}>
                <div><strong>{i.title_plain}</strong></div>
                <div style={{ fontSize: 12, opacity: 0.7 }}>{SEVERITY_LABEL[i.severity]}</div>
                <div style={{ fontSize: 13, margin: "4px 0 8px" }}>{i.impact_plain}</div>
                <button type="button" onClick={() => askOra(i)} style={{ marginRight: 8 }}>Ask ORA to help</button>
                <button type="button" onClick={() => onIgnore(i.id)}>Ignore</button>
              </div>
            ))}
          </section>
        );
      })}
    </aside>
  );
}
