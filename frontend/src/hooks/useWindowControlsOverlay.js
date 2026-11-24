// useWindowControlsOverlay — Desktop detect-only hook.
//
// Exposes the Window Controls Overlay state for installed desktop PWAs
// (Chromium only). Detection only: it never renders a title bar or any
// UI. Components should gate any overlay-aware layout on
// `supported && visible`.
//
// API: `navigator.windowControlsOverlay`
//   - `.visible` — true while the PWA runs with the overlay enabled.
//   - `.getTitlebarAreaRect()` — the title-bar area geometry.
//   - `geometrychange` event — fires when the overlay geometry changes
//     (window resize, maximise, snap). The handler is debounced so a
//     drag-resize does not re-render on every event.
import { useEffect, useState } from "react";

// Geometry events fire continuously while a window is drag-resized;
// coalesce them into a single state update per burst.
const GEOMETRY_DEBOUNCE_MS = 100;

function readOverlayState() {
  if (typeof navigator === "undefined" || !("windowControlsOverlay" in navigator)) {
    return { supported: false, visible: false, titlebarArea: null };
  }
  const overlay = navigator.windowControlsOverlay;
  const visible = overlay.visible === true;
  let titlebarArea = null;
  if (visible && typeof overlay.getTitlebarAreaRect === "function") {
    const rect = overlay.getTitlebarAreaRect();
    titlebarArea = { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  }
  return { supported: true, visible, titlebarArea };
}

export function useWindowControlsOverlay() {
  const [state, setState] = useState(readOverlayState);

  useEffect(() => {
    if (typeof navigator === "undefined" || !("windowControlsOverlay" in navigator)) {
      return undefined;
    }
    const overlay = navigator.windowControlsOverlay;
    let timerId = null;
    const scheduleUpdate = () => {
      if (timerId !== null) {
        clearTimeout(timerId);
      }
      timerId = setTimeout(() => {
        timerId = null;
        setState(readOverlayState());
      }, GEOMETRY_DEBOUNCE_MS);
    };
    // Publish the current geometry on mount — it may have changed
    // between the initial state read and the listener attaching.
    setState(readOverlayState());
    overlay.addEventListener("geometrychange", scheduleUpdate);
    return () => {
      overlay.removeEventListener("geometrychange", scheduleUpdate);
      if (timerId !== null) {
        clearTimeout(timerId);
      }
    };
  }, []);

  return state;
}
