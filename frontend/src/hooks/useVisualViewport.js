// Phase 1 mobile view (2026-11-24) -- track the on-screen keyboard via the
// Visual Viewport API and expose it as CSS variables so the composer can
// rise above the keyboard and the chat area can shrink.
//
// iOS Safari does NOT resize the layout viewport when the keyboard opens
// (it overlays it), so a `100dvh` layout leaves the composer hidden behind
// the keyboard. `window.visualViewport.height` shrinks by exactly the
// keyboard height; the difference between the layout height and the visual
// height is the keyboard height. Android Chrome with
// `interactive-widget=resizes-content` resizes the layout viewport instead,
// so this hook reports a 0px keyboard there and the layout reflows on its own.
import { useEffect, useState } from "react";

function readViewport() {
  if (typeof window === "undefined" || !window.visualViewport) {
    return { height: 0, keyboardHeight: 0, isKeyboardOpen: false };
  }
  const vv = window.visualViewport;
  const layoutHeight = window.innerHeight;
  const height = Math.round(vv.height);
  // The keyboard is the part of the layout viewport the visual viewport no
  // longer covers. Ignore tiny deltas (browser chrome showing/hiding).
  const rawKeyboardHeight = Math.max(0, layoutHeight - height);
  const isKeyboardOpen = rawKeyboardHeight > 40;
  return {
    height,
    keyboardHeight: isKeyboardOpen ? rawKeyboardHeight : 0,
    isKeyboardOpen,
  };
}

// Number of mounted hook instances. The CSS variables are shared state:
// they must only be cleared when the LAST instance unmounts, otherwise one
// unmounting surface (e.g. the tab bar hiding while the keyboard is open)
// would wipe the values another surface (e.g. the composer) still needs.
let activeInstances = 0;

export function useVisualViewport() {
  const [viewport, setViewport] = useState(readViewport);

  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return undefined;
    const update = () => setViewport(readViewport());
    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
    };
  }, []);

  // Publish the measurements as CSS variables on :root so any surface can
  // react without re-rendering (e.g. `padding-bottom: var(--kb-height, 0px)`).
  useEffect(() => {
    const root = document.documentElement;
    activeInstances += 1;
    root.style.setProperty("--kb-height", `${viewport.keyboardHeight}px`);
    root.style.setProperty("--vv-height", `${viewport.height}px`);
    return () => {
      activeInstances -= 1;
      if (activeInstances <= 0) {
        activeInstances = 0;
        root.style.setProperty("--kb-height", "0px");
        root.style.removeProperty("--vv-height");
      }
    };
  }, [viewport.keyboardHeight, viewport.height]);

  return viewport;
}

export default useVisualViewport;
