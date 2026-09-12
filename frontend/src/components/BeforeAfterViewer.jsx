/**
 * BeforeAfterViewer.jsx — 2026-09-12
 * Side-by-Side vs Slider-Overlay (wipe) comparison of the uploaded
 * "before" screenshot vs the headless-captured "after" preview.
 * Static images only (v1) — no video/animation.
 */
import { useState } from "react";
import { Columns2, MoveHorizontal } from "lucide-react";

export const BeforeAfterViewer = ({ beforeUrl, afterUrl }) => {
  const [mode, setMode] = useState("split"); // "split" | "slider"
  const [slider, setSlider] = useState(50);

  return (
    <div data-testid="before-after-viewer" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 6 }}>
        <button type="button" data-testid="before-after-mode-split" onClick={() => setMode("split")}
          style={{ ...tabStyle, ...(mode === "split" ? tabActive : {}) }}>
          <Columns2 size={12} /> Side-by-side
        </button>
        <button type="button" data-testid="before-after-mode-slider" onClick={() => setMode("slider")}
          style={{ ...tabStyle, ...(mode === "slider" ? tabActive : {}) }}>
          <MoveHorizontal size={12} /> Slider
        </button>
      </div>

      {mode === "split" ? (
        <div style={{ display: "flex", gap: 8 }}>
          <Frame label="Before" url={beforeUrl} testId="before-after-before-frame" />
          <Frame label="After" url={afterUrl} testId="before-after-after-frame" />
        </div>
      ) : (
        <div style={{ position: "relative", width: "100%", maxWidth: 480, aspectRatio: "16/10",
                      borderRadius: 8, overflow: "hidden", border: "1px solid rgba(255,255,255,0.12)" }}>
          <img src={beforeUrl} alt="Before" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />
          <div style={{ position: "absolute", inset: 0, width: `${slider}%`, overflow: "hidden" }}>
            <img src={afterUrl} alt="After" style={{ width: `${10000 / slider}%`, maxWidth: "none", height: "100%", objectFit: "cover" }} />
          </div>
          <input
            type="range" min={0} max={100} value={slider}
            data-testid="before-after-slider-input"
            onChange={(e) => setSlider(Number(e.target.value))}
            style={{ position: "absolute", bottom: 6, left: 6, right: 6, width: "calc(100% - 12px)" }}
          />
        </div>
      )}
    </div>
  );
};

const Frame = ({ label, url, testId }) => (
  <div style={{ flex: 1 }}>
    <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>{label}</div>
    {url ? (
      <img data-testid={testId} src={url} alt={label}
           style={{ width: "100%", borderRadius: 8, border: "1px solid rgba(255,255,255,0.12)" }} />
    ) : (
      <div data-testid={testId} style={{ width: "100%", aspectRatio: "16/10", borderRadius: 8,
                    border: "1px dashed rgba(255,255,255,0.15)", display: "flex",
                    alignItems: "center", justifyContent: "center", fontSize: 12, opacity: 0.5 }}>
        Not available
      </div>
    )}
  </div>
);

const tabStyle = {
  display: "flex", alignItems: "center", gap: 4, padding: "4px 10px", borderRadius: 999,
  background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)",
  color: "var(--text-dim)", fontSize: 12, cursor: "pointer",
};
const tabActive = { background: "rgba(255,102,8,0.15)", borderColor: "rgba(255,102,8,0.4)", color: "#FF6608" };

export default BeforeAfterViewer;
