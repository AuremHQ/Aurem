/**
 * ThinkingLoader.jsx — "Infinity Loop — Thinking" indicator (2026-09-08)
 *
 * Small inline replacement for the generic Loader2 spin icon wherever
 * "ORA is thinking…" renders. Pure SVG + SMIL animateMotion — no JS
 * animation loop, no extra dependency.
 *
 * Design spec (as approved):
 *   - Infinity-shape (∞) path, thin stroke.
 *   - Leading particle #00E5FF (bright, glowing), trailing particle
 *     #00BFA5 (dimmer, a beat behind) for the dual-stream/tail effect.
 *   - Ease-in-out motion (not linear), ~3s loop, indefinite repeat.
 *   - No background/gradient container baked in — this is an ICON,
 *     it inherits whatever surface it's dropped onto (chat bubble,
 *     narration line, etc). Forcing the dark #0A1128→#12192C brand
 *     gradient behind a small inline icon would fight the existing
 *     light/dark chat theme, so it's intentionally omitted here.
 *   - No internal "thinking" text label — every call site already
 *     shows its own text next to the icon (friendlyPhrase, narration
 *     line text, etc), so baking in a second label would duplicate it.
 *   - Below ~28px, the trailing/tail particle reads as visual noise
 *     at that scale, so it's dropped automatically (single glowing
 *     dot only) — see SINGLE_PARTICLE_BELOW.
 *   - Reads `data-theme` on <html> to hold contrast on the light
 *     theme: the brand cyan/teal on their own wash out badly against
 *     a white chat surface (verified — the dark-only glow was
 *     designed against #0A1128), so light mode gets a thin dark edge
 *     ring on each particle + a darker/more-opaque track, instead of
 *     forcing the dark gradient container the spec said not to.
 *
 *   <ThinkingLoader size={16} />   // single-particle (below the 28px
 *                                  // dual-stream legibility threshold)
 *   <ThinkingLoader size={36} />   // dual-particle (>= 28px, tail visible)
 */
import { useId, useEffect, useState } from "react";

const SINGLE_PARTICLE_BELOW = 28;

function useIsLightTheme() {
  // Deterministic default at first paint (dark theme is the primary/
  // first-choice design) — the real value is read post-mount inside
  // the effect below, never as a useState initializer, so prerendered
  // markup and the client's first paint always agree (no flash/pop).
  const [light, setLight] = useState(false);
  useEffect(() => {
    const root = document.documentElement;
    setLight(root.getAttribute("data-theme") === "light");
    const obs = new MutationObserver(() => setLight(root.getAttribute("data-theme") === "light"));
    obs.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return light;
}

export default function ThinkingLoader({ size = 18, style, "aria-label": ariaLabel = "ORA is thinking" }) {
  const uid = useId().replace(/[:]/g, "");
  const pathId = `thinking-inf-path-${uid}`;
  const glowId = `thinking-inf-glow-${uid}`;
  const light = useIsLightTheme();
  const dual = size >= SINGLE_PARTICLE_BELOW;
  // viewBox is fixed 2:1 (infinity glyphs read wider than tall);
  // rendered width/height scale from `size` so callers only pass one
  // number, same ergonomics as the Loader2 icon it replaces.
  const vbW = 100, vbH = 50;
  const strokeW = dual ? 3.2 : 4.2; // thinner track at larger sizes reads cleaner
  const edgeStroke = light ? "rgba(10,17,40,0.45)" : "none";
  const edgeWidth = light ? 0.9 : 0;

  return (
    <svg
      data-testid="thinking-loader"
      data-theme-variant={light ? "light" : "dark"}
      aria-label={ariaLabel}
      role="img"
      width={size * 2}
      height={size}
      viewBox={`0 0 ${vbW} ${vbH}`}
      style={{ display: "inline-block", flexShrink: 0, verticalAlign: "middle", ...style }}
    >
      <defs>
        <filter id={glowId} x="-200%" y="-200%" width="500%" height="500%">
          <feGaussianBlur stdDeviation={light ? 1.1 : 1.6} result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      {/* Faint track so the ∞ shape reads even between particle passes. */}
      <path
        id={pathId}
        d="M25,25 C25,9 41,9 50,25 C59,41 75,41 75,25 C75,9 59,9 50,25 C41,41 25,41 25,25 Z"
        fill="none"
        stroke={light ? "#0092A8" : "#00E5FF"}
        strokeOpacity={light ? 0.38 : 0.16}
        strokeWidth={strokeW}
        strokeLinecap="round"
      />
      {/* Leading particle — bright cyan, glowing. Thin dark edge ring
          in light mode so it doesn't wash out against a white surface. */}
      <circle r={dual ? 3.4 : 4.4} fill="#00E5FF" stroke={edgeStroke} strokeWidth={edgeWidth} filter={`url(#${glowId})`}>
        <animateMotion
          dur="3s"
          repeatCount="indefinite"
          calcMode="spline"
          keyPoints="0;1"
          keyTimes="0;1"
          keySplines="0.42 0 0.58 1"
        >
          <mpath href={`#${pathId}`} />
        </animateMotion>
      </circle>
      {/* Trailing particle — dimmer teal, a beat behind (tail effect). */}
      {dual && (
        <circle r={2.6} fill="#00BFA5" stroke={edgeStroke} strokeWidth={edgeWidth} filter={`url(#${glowId})`}>
          <animateMotion
            dur="3s"
            begin="0.18s"
            repeatCount="indefinite"
            calcMode="spline"
            keyPoints="0;1"
            keyTimes="0;1"
            keySplines="0.42 0 0.58 1"
          >
            <mpath href={`#${pathId}`} />
          </animateMotion>
        </circle>
      )}
    </svg>
  );
}
