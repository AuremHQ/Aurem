import { useEffect } from "react";

// For overlays that don't use useModalA11y (no focus trap needed).
export function useEscapeKey(onEscape, active = true) {
  useEffect(() => {
    if (!active || !onEscape) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onEscape(e); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onEscape, active]);
}

export default useEscapeKey;
