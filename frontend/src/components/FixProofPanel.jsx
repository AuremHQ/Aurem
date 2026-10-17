/**
 * FixProofPanel.jsx — Ticket D-UI (2026-02): the before/after visual
 * proof the user actually watches, rendered inside the fix-complete
 * card (FinalSummaryCard) inside FixProgressDrawer.
 *
 * Fetches GET /fix-pipeline/proof/{job_id}/meta first (cheap JSON) to
 * decide which of 4 states to render — never fetches image bytes
 * speculatively:
 *   1. loading           — meta request in flight
 *   2. empty             — `attempted: false` (no preview_url configured
 *                           on the project — proof was never attempted)
 *   3. could-not-verify  — attempted, but this device's capture failed
 *                           (SSRF-blocked / timeout / crashed) — NEVER
 *                           implies success, names the real reason
 *   4. success           — real before/after images, side by side on
 *                           desktop, stacked on narrow/mobile widths
 *
 * Owner-only — the backend /proof endpoints already 404 for a job that
 * isn't the caller's own; this component just surfaces whatever the
 * backend honestly returns.
 */
import React, { useEffect, useState } from "react";
import { Loader2, ImageOff, Monitor, Smartphone, GitCompareArrows } from "lucide-react";
import { api } from "../lib/api";

function humanizeFailReason(reason) {
  if (!reason) return "the visual check could not complete for this device.";
  if (reason.includes("blocked_ssrf")) return "the preview URL points to a blocked/internal address.";
  if (reason.includes("timeout")) return "the preview page took too long to respond.";
  if (reason.includes("crashed")) return "the visual check crashed unexpectedly.";
  return `the visual check failed (${reason}).`;
}

export function FixProofPanel({ jobId }) {
  const [meta, setMeta] = useState(undefined); // undefined = loading
  const [device, setDevice] = useState("desktop");
  const [imgUrls, setImgUrls] = useState({}); // { desktop: {before, after}, mobile: {...} }

  useEffect(() => {
    if (!jobId) return undefined;
    let gone = false;
    setMeta(undefined);
    (async () => {
      try {
        const r = await api.get(`/fix-pipeline/proof/${jobId}/meta`);
        if (!gone) setMeta(r.data);
      } catch {
        if (!gone) setMeta({ ok: false, attempted: false });
      }
    })();
    return () => { gone = true; };
  }, [jobId]);

  useEffect(() => {
    if (!meta?.attempted || !jobId) return undefined;
    const d = meta.devices?.[device];
    if (!d?.has_images || imgUrls[device]) return undefined;
    let gone = false;
    (async () => {
      const next = {};
      for (const variant of ["before", "after"]) {
        try {
          const r = await api.get(
            `/fix-pipeline/proof/${jobId}?variant=${variant}&device=${device}`,
            { responseType: "blob" },
          );
          next[variant] = URL.createObjectURL(r.data);
        } catch { /* leave this variant missing — figure shows its own loader */ }
      }
      if (!gone) setImgUrls((prev) => ({ ...prev, [device]: next }));
    })();
    return () => { gone = true; };
  }, [meta, device, jobId, imgUrls]);

  if (!jobId) return null;

  if (meta === undefined) {
    return (
      <div data-testid="fix-proof-panel" className="fix-proof-panel" style={panelStyle}>
        <div data-testid="fix-proof-loading" role="status" aria-live="polite" style={rowStyle}>
          <Loader2 size={13} className="anim-spin" aria-hidden="true" />
          Checking for before/after proof…
        </div>
      </div>
    );
  }

  if (!meta.attempted) {
    return (
      <div data-testid="fix-proof-panel" className="fix-proof-panel" style={panelStyle}>
        <div data-testid="fix-proof-empty" role="status" style={{ ...rowStyle, color: "#94a3b8" }}>
          <ImageOff size={13} aria-hidden="true" />
          No live preview configured for this project — before/after proof wasn&apos;t attempted.
        </div>
      </div>
    );
  }

  const d = meta.devices?.[device] || {};
  const imgs = imgUrls[device] || {};

  return (
    <div data-testid="fix-proof-panel" className="fix-proof-panel" style={panelStyle}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8, flexWrap: "wrap", gap: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: "#cbd5e1", display: "flex", alignItems: "center", gap: 6 }}>
          <GitCompareArrows size={13} aria-hidden="true" /> Before / after proof
        </span>
        <div role="tablist" aria-label="Device" style={{ display: "flex", gap: 4 }}>
          {["desktop", "mobile"].map((dv) => (
            <button
              key={dv}
              type="button"
              role="tab"
              aria-selected={device === dv}
              aria-label={`${dv === "desktop" ? "Desktop" : "Mobile"} view`}
              data-testid={`fix-proof-device-${dv}`}
              onClick={() => setDevice(dv)}
              style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "3px 8px", borderRadius: 999, fontSize: 10,
                fontWeight: 600, cursor: "pointer",
                background: device === dv ? "rgba(125,211,252,0.15)" : "rgba(255,255,255,0.03)",
                border: `1px solid ${device === dv ? "rgba(125,211,252,0.4)" : "rgba(255,255,255,0.10)"}`,
                color: device === dv ? "#7dd3fc" : "#94a3b8",
              }}
            >
              {dv === "desktop" ? <Monitor size={11} aria-hidden="true" /> : <Smartphone size={11} aria-hidden="true" />}
              {dv === "desktop" ? "Desktop" : "Mobile"}
            </button>
          ))}
        </div>
      </div>

      {!d.has_images ? (
        <div data-testid="fix-proof-could-not-verify" role="status" style={{ ...rowStyle, color: "#fca5a5" }}>
          <ImageOff size={13} aria-hidden="true" />
          Could not verify on {device} — {humanizeFailReason(d.before_fail_reason || d.after_fail_reason)}
        </div>
      ) : (
        <>
          <div className="fix-proof-grid" data-testid="fix-proof-images">
            <figure style={figureStyle}>
              <figcaption style={captionStyle}>Before</figcaption>
              {imgs.before
                ? <img data-testid={`fix-proof-img-before-${device}`} src={imgs.before}
                       alt={`Before screenshot (${device})`} style={imgStyle} />
                : <div data-testid="fix-proof-img-loading-before" style={imgPlaceholderStyle}><Loader2 size={14} className="anim-spin" aria-hidden="true" /></div>}
            </figure>
            <figure style={figureStyle}>
              <figcaption style={captionStyle}>After</figcaption>
              {imgs.after
                ? <img data-testid={`fix-proof-img-after-${device}`} src={imgs.after}
                       alt={`After screenshot (${device})`} style={imgStyle} />
                : <div data-testid="fix-proof-img-loading-after" style={imgPlaceholderStyle}><Loader2 size={14} className="anim-spin" aria-hidden="true" /></div>}
            </figure>
          </div>
          <div data-testid="fix-proof-diff-percent" style={{ marginTop: 6, fontSize: 10, color: "#94a3b8", fontFamily: "'JetBrains Mono', monospace" }}>
            {d.changed
              ? `${Number(d.diff_percent ?? 0).toFixed(1)}% visual difference`
              : "No visual change detected"}
          </div>
        </>
      )}
    </div>
  );
}

const panelStyle = {
  marginTop: 10, padding: 12, borderRadius: 10,
  background: "rgba(255,255,255,0.02)",
  border: "1px solid rgba(255,255,255,0.08)",
};
const rowStyle = {
  display: "flex", alignItems: "center", gap: 8, fontSize: 11,
  fontFamily: "'JetBrains Mono', monospace",
};
const figureStyle = { flex: "1 1 160px", minWidth: 0, margin: 0 };
const captionStyle = { fontSize: 9, color: "#64748b", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.06em" };
const imgStyle = { width: "100%", borderRadius: 8, border: "1px solid rgba(255,255,255,0.08)", display: "block" };
const imgPlaceholderStyle = {
  width: "100%", aspectRatio: "16 / 10", borderRadius: 8,
  background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)",
  display: "flex", alignItems: "center", justifyContent: "center",
};
